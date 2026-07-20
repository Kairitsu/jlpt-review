import importlib
import json
import sqlite3
import uuid


def load_app(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("APP_SECRET", "test-secret")
    monkeypatch.setenv("SESSION_COOKIE_SECURE", "false")
    monkeypatch.setenv("TRUST_PROXY_COUNT", "0")
    import app
    import db

    importlib.reload(db)
    importlib.reload(app)
    return app.create_app({"TESTING": True, "FSRS_ENABLE_FUZZING": False}).test_client(), db


def create_legacy_sentence(client, collection_id, chinese, japanese, chunks):
    response = client.post("/api/sentences", json={
        "collectionId": collection_id,
        "chinese": chinese,
        "japanese": japanese,
        "chunks": chunks,
        "correctOrder": [chunk["id"] for chunk in chunks],
    })
    assert response.status_code == 201
    return response.get_json()["sentence"]


def test_copy_migration_preserves_history_fsrs_and_manual_boundaries(tmp_path, monkeypatch):
    from scripts.migrate_ginza_chunks import (
        backup_database,
        migrate_rows,
        protected_snapshot,
        validate_all_rows,
    )

    client, db_module = load_app(tmp_path, monkeypatch)
    collection_id = client.get("/api/dashboard").get_json()["collections"][0]["id"]
    automatic = create_legacy_sentence(
        client, collection_id, "我学习日语。", "私は、日本語を勉強しています。",
        [
            {"id": "old-1", "text": "私は"}, {"id": "old-p1", "text": "、"},
            {"id": "old-2", "text": "日本語を"}, {"id": "old-3", "text": "勉強しています"},
            {"id": "old-p2", "text": "。"},
        ],
    )
    manual = create_legacy_sentence(
        client, collection_id, "他说再见。", "僕が「さよなら」と言った。",
        [
            {"id": "manual-a", "text": "僕が"},
            {"id": "manual-b", "text": "「さよなら」"},
            {"id": "manual-c", "text": "と言った"},
            {"id": "manual-p", "text": "。"},
        ],
    )

    practice = client.post("/api/practice/sessions", json={"sentenceIds": [automatic["id"]]}).get_json()
    sentence = practice["sentences"][0]
    attempt = client.post(
        f'/api/practice/sessions/{practice["sessionId"]}/attempts',
        json={
            "attemptId": str(uuid.uuid4()), "sentenceId": sentence["id"],
            "action": "check", "answerOrder": sentence["correctOrder"],
        },
    )
    assert attempt.get_json()["status"] == "correct"
    assert client.post(f'/api/practice/sessions/{practice["sessionId"]}/complete', json={}).status_code == 200

    database = tmp_path / "japanese_sentence_review.sqlite3"
    legacy_rows = {
        automatic["id"]: [
            {"id": "a1", "text": "私"}, {"id": "a2", "text": "は"},
            {"id": "ap1", "text": "、"}, {"id": "a3", "text": "日本語"},
            {"id": "a4", "text": "を"}, {"id": "a5", "text": "勉強しています"},
            {"id": "ap2", "text": "。"},
        ],
        manual["id"]: [
            {"id": "manual-a", "text": "僕が"},
            {"id": "manual-b", "text": "「さよなら」"},
            {"id": "manual-c", "text": "と言った"},
            {"id": "manual-p", "text": "。"},
        ],
    }
    with sqlite3.connect(database) as connection:
        for sentence_id, chunks in legacy_rows.items():
            connection.execute(
                """UPDATE sentences SET chunks_json=?,correct_order_json=?,
                   practice_structure_json='[]',chunk_source='legacy',
                   chunk_schema_version=1,chunks_manually_edited=0 WHERE id=?""",
                (json.dumps(chunks, ensure_ascii=False), json.dumps([c["id"] for c in chunks]), sentence_id),
            )

    backup = backup_database(database, tmp_path / "backups")
    assert backup.is_file()
    with sqlite3.connect(database) as connection:
        connection.row_factory = sqlite3.Row
        before = protected_snapshot(connection)
        connection.execute("BEGIN IMMEDIATE")
        stats = migrate_rows(connection, preserve_manual=True)
        connection.commit()
        after = protected_snapshot(connection)
        validation = validate_all_rows(connection)
        manual_row = connection.execute(
            "SELECT chunks_json,practice_structure_json,chunk_source FROM sentences WHERE id=?",
            (manual["id"],),
        ).fetchone()

    assert stats.total == 2 and stats.migrated == 2 and stats.failed == 0
    assert stats.manual_preserved == 1 and stats.ginza == 1
    assert before["counts"] == after["counts"]
    assert before["hashes"] == after["hashes"]
    assert validation == {"valid": True, "failures": [], "punctuation_chunks": 0, "fallback_rows": 0}
    migrated_manual = json.loads(manual_row["chunks_json"])
    assert [chunk["text"] for chunk in migrated_manual] == ["僕が", "さよなら", "と言った"]
    assert manual_row["chunk_source"] == "manual_migrated"

    migrated = client.get(f'/api/sentences/{manual["id"]}').get_json()["sentence"]
    retry = client.post("/api/practice/sessions", json={"sentenceIds": [manual["id"]]}).get_json()
    checked = client.post(
        f'/api/practice/sessions/{retry["sessionId"]}/attempts',
        json={
            "attemptId": str(uuid.uuid4()), "sentenceId": manual["id"],
            "action": "check", "answerOrder": migrated["correctOrder"],
        },
    )
    assert checked.get_json()["status"] == "correct"
    assert client.post(f'/api/practice/sessions/{retry["sessionId"]}/complete', json={}).status_code == 200
    report = client.get(f'/api/reports/{retry["sessionId"]}').get_json()["report"]
    assert report["items"][0]["japanese"] == "僕が「さよなら」と言った。"
    assert report["items"][0]["status"] == "correct"


def test_attempt_grades_against_session_snapshot_after_live_rechunk(tmp_path, monkeypatch):
    client, db_module = load_app(tmp_path, monkeypatch)
    collection_id = client.get("/api/dashboard").get_json()["collections"][0]["id"]
    sentence = create_legacy_sentence(
        client, collection_id, "猫在这里。", "猫がここにいる。",
        [{"id": "a", "text": "猫が"}, {"id": "b", "text": "ここにいる"}, {"id": "p", "text": "。"}],
    )
    practice = client.post("/api/practice/sessions", json={"sentenceIds": [sentence["id"]]}).get_json()
    snapshot_sentence = practice["sentences"][0]

    with db_module.get_db() as connection:
        connection.execute(
            "UPDATE sentences SET chunks_json=?,correct_order_json=? WHERE id=?",
            (
                json.dumps([{"id": "replacement", "text": "猫がここにいる", "start": 0, "end": 7}], ensure_ascii=False),
                json.dumps(["replacement"]), sentence["id"],
            ),
        )

    response = client.post(
        f'/api/practice/sessions/{practice["sessionId"]}/attempts',
        json={
            "attemptId": str(uuid.uuid4()), "sentenceId": sentence["id"],
            "action": "check", "answerOrder": snapshot_sentence["correctOrder"],
        },
    )
    assert response.status_code == 200
    assert response.get_json()["status"] == "correct"
