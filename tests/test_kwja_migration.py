import importlib
import json
import sqlite3
import uuid


FSRS_FIELDS = (
    "fsrs_state",
    "fsrs_step",
    "stability",
    "difficulty",
    "last_review_at",
    "next_review_at",
    "fsrs_version",
)


def load_app(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("APP_SECRET", "test-secret")
    monkeypatch.setenv("SESSION_COOKIE_SECURE", "false")
    monkeypatch.setenv("TRUST_PROXY_COUNT", "0")
    import app
    import db

    importlib.reload(db)
    importlib.reload(app)
    return app.create_app(
        {"TESTING": True, "FSRS_ENABLE_FUZZING": False}
    ).test_client(), db


def create_sentence(client, collection_id):
    organized = client.post(
        "/api/sentences/organize",
        json={"chinese": "我学习日语。", "japanese": "私は、日本語を勉強しています。"},
    ).get_json()
    response = client.post(
        "/api/sentences",
        json={
            "collectionId": collection_id,
            "chinese": "我学习日语。",
            "japanese": "私は、日本語を勉強しています。",
            "chunks": organized["chunks"],
            "correctOrder": organized["correctOrder"],
            "practiceStructure": organized["practiceStructure"],
            "chunkSource": organized["source"],
        },
    )
    assert response.status_code == 201
    return response.get_json()["sentence"]


def table_counts(database):
    with sqlite3.connect(database) as connection:
        return {
            table: connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in (
                "sentences",
                "practice_sessions",
                "practice_items",
                "attempts",
                "review_events",
            )
        }


def test_resumable_kwja_migration_preserves_history_fsrs_and_replaces_manual_chunks(
    tmp_path, monkeypatch
):
    client, db_module = load_app(tmp_path, monkeypatch)
    collection_id = client.get("/api/dashboard").get_json()["collections"][0]["id"]
    sentence = create_sentence(client, collection_id)
    practice = client.post(
        "/api/practice/sessions", json={"sentenceIds": [sentence["id"]]}
    ).get_json()
    snapshot = practice["sentences"][0]
    attempt = client.post(
        f'/api/practice/sessions/{practice["sessionId"]}/attempts',
        json={
            "attemptId": str(uuid.uuid4()),
            "sentenceId": sentence["id"],
            "action": "check",
            "answerOrder": snapshot["correctOrder"],
        },
    )
    assert attempt.get_json()["status"] == "correct"
    assert client.post(
        f'/api/practice/sessions/{practice["sessionId"]}/complete', json={}
    ).status_code == 200

    database = tmp_path / "japanese_sentence_review.sqlite3"
    counts_before = table_counts(database)
    with db_module.get_db() as connection:
        order_before = dict(
            connection.execute(
                """SELECT * FROM practice_cards
                   WHERE sentence_id=? AND card_type='sentence_order' AND active=1""",
                (sentence["id"],),
            ).fetchone()
        )
        connection.execute(
            """UPDATE sentences SET chunks_json=?,correct_order_json=?,
                 practice_structure_json='[]',chunk_source='manual',
                 chunk_schema_version=2,chunks_manually_edited=1
               WHERE id=?""",
            (
                json.dumps(
                    [
                        {
                            "id": "legacy-manual",
                            "text": sentence["japanese"],
                            "start": 0,
                            "end": len(sentence["japanese"]),
                        }
                    ],
                    ensure_ascii=False,
                ),
                json.dumps(["legacy-manual"]),
                sentence["id"],
            ),
        )

    import scripts.migrate_kwja as migration

    importlib.reload(migration)
    migration.configure_database(database)
    db_module.init_db(enable_fuzzing=False)
    assert migration.seed_queue(force_all=True) == 1

    # A process killed after claiming work must be put back into the queue.
    with db_module.get_db() as connection:
        connection.execute(
            "UPDATE kwja_migration_items SET status='processing' WHERE sentence_id=?",
            (sentence["id"],),
        )
    migration.seed_queue(force_all=False)
    with db_module.get_db() as connection:
        assert connection.execute(
            "SELECT status FROM kwja_migration_items WHERE sentence_id=?",
            (sentence["id"],),
        ).fetchone()["status"] == "pending"

    result = migration.process_sentence(sentence["id"])
    assert result["status"] == "success"
    validation = migration.validate_complete()
    assert validation["ok"] is True
    assert validation["sentenceOrderCards"] == 1
    assert validation["foreignKeyErrors"] == 0
    assert validation["invalidReconstruction"] == 0
    assert table_counts(database) == counts_before

    with db_module.get_db() as connection:
        migrated = connection.execute(
            "SELECT * FROM sentences WHERE id=?", (sentence["id"],)
        ).fetchone()
        order_after = dict(
            connection.execute(
                """SELECT * FROM practice_cards
                   WHERE sentence_id=? AND card_type='sentence_order' AND active=1""",
                (sentence["id"],),
            ).fetchone()
        )
        audit = connection.execute(
            "SELECT * FROM kwja_migration_items WHERE sentence_id=?",
            (sentence["id"],),
        ).fetchone()
        card_count = connection.execute(
            "SELECT COUNT(*) FROM practice_cards WHERE sentence_id=?",
            (sentence["id"],),
        ).fetchone()[0]
    assert migrated["chunk_source"] == "kwja_tiny_phrase"
    assert migrated["chunk_schema_version"] == 3
    assert migrated["chunks_manually_edited"] == 0
    assert audit["old_chunks_manually_edited"] == 1
    assert audit["old_analysis_source"] == "manual"
    assert all(order_after[field] == order_before[field] for field in FSRS_FIELDS)

    # A repeated completed migration is a no-op and cannot duplicate cards.
    migration.seed_queue(force_all=False)
    assert migration.process_sentence(sentence["id"])["status"] == "skipped"
    with db_module.get_db() as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM practice_cards WHERE sentence_id=?",
            (sentence["id"],),
        ).fetchone()[0] == card_count

