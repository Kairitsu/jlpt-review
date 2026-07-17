import importlib
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest
from fsrs import Rating, Scheduler, State

from fsrs_service import (
    LEARNING_STEPS,
    RELEARNING_STEPS,
    card_fields,
    new_card,
    parse_utc,
    review,
    scheduler,
)


NOW = datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc)


def row_for(card):
    return {"id": card.card_id, **card_fields(card)}


def reviewed_row(rating=Rating.Good):
    outcome = review(
        row_for(new_card(1, NOW)),
        rating,
        reviewed_at=NOW,
        enable_fuzzing=False,
    )
    return {"id": 1, **outcome.after}, outcome


def load_app(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("APP_SECRET", "test-secret")
    monkeypatch.setenv("SESSION_COOKIE_SECURE", "false")
    monkeypatch.setenv("TRUST_PROXY_COUNT", "0")
    import app
    import db

    importlib.reload(db)
    importlib.reload(app)
    return app.create_app({"TESTING": True}).test_client(), db


def make_sentence(client, collection_id, suffix):
    chunk_id = f"fsrs-{suffix}"
    response = client.post("/api/sentences", json={
        "collectionId": collection_id,
        "chinese": f"迁移句子 {suffix}",
        "japanese": f"文{suffix}",
        "chunks": [{"id": chunk_id, "text": f"文{suffix}"}],
        "correctOrder": [chunk_id],
    })
    assert response.status_code == 201
    return response.get_json()["sentence"]


def complete_good(client, sentence):
    practice = client.post(
        "/api/practice/sessions", json={"sentenceIds": [sentence["id"]]}
    ).get_json()
    session_id = practice["sessionId"]
    attempt = client.post(f"/api/practice/sessions/{session_id}/attempts", json={
        "sentenceId": sentence["id"],
        "action": "check",
        "answerOrder": sentence["correctOrder"],
        "durationMs": 800,
    })
    assert attempt.status_code == 200
    finalized = client.post(
        f"/api/practice/sessions/{session_id}/sentences/{sentence['id']}/complete",
        json={},
    )
    assert finalized.status_code == 200
    assert finalized.get_json()["rating"] == "good"
    assert client.post(f"/api/practice/sessions/{session_id}/complete", json={}).status_code == 200
    return session_id


def table_rows(connection, table):
    return [tuple(row) for row in connection.execute(f"SELECT * FROM {table} ORDER BY rowid")]


def preserved_counts(connection):
    counts = {
        table: connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        for table in (
            "collections",
            "sentences",
            "review_events",
            "attempts",
            "practice_sessions",
            "practice_items",
        )
    }
    counts["reports"] = connection.execute(
        """SELECT COUNT(*) FROM practice_sessions
           WHERE completed_at IS NOT NULL AND report_deleted_at IS NULL"""
    ).fetchone()[0]
    return counts


def test_scheduler_has_no_short_steps_and_keeps_default_model_parameters():
    configured = scheduler()
    package_defaults = Scheduler()
    assert LEARNING_STEPS == () and configured.learning_steps == ()
    assert RELEARNING_STEPS == () and configured.relearning_steps == ()
    assert configured.desired_retention == 0.90
    assert configured.maximum_interval == 36500
    assert configured.enable_fuzzing is True
    assert len(configured.parameters) == 21
    assert configured.parameters == package_defaults.parameters


def test_new_card_good_enters_review_without_minute_due_time():
    _, outcome = reviewed_row(Rating.Good)
    due = parse_utc(outcome.after["next_review_at"])
    assert outcome.after["fsrs_state"] == int(State.Review)
    assert outcome.after["fsrs_step"] is None
    assert due - NOW >= timedelta(days=1)
    assert due > NOW + timedelta(minutes=10)


def test_new_card_hard_enters_review_and_is_due_no_sooner_than_a_day():
    _, outcome = reviewed_row(Rating.Hard)
    due = parse_utc(outcome.after["next_review_at"])
    assert outcome.after["fsrs_state"] == int(State.Review)
    assert outcome.after["fsrs_step"] is None
    assert due - NOW >= timedelta(days=1)


def test_consecutive_good_reviews_extend_the_overall_interval():
    current = row_for(new_card(1, NOW))
    reviewed_at = NOW
    intervals = []
    for _ in range(4):
        outcome = review(
            current,
            Rating.Good,
            reviewed_at=reviewed_at,
            enable_fuzzing=False,
        )
        due = parse_utc(outcome.after["next_review_at"])
        intervals.append(due - reviewed_at)
        current = {"id": 1, **outcome.after}
        reviewed_at = due

    assert all(later > earlier for earlier, later in zip(intervals, intervals[1:]))


def test_same_review_card_and_time_schedule_hard_before_good():
    current, first = reviewed_row(Rating.Good)
    reviewed_at = parse_utc(first.after["next_review_at"])
    hard = review(current, Rating.Hard, reviewed_at=reviewed_at, enable_fuzzing=False)
    good = review(current, Rating.Good, reviewed_at=reviewed_at, enable_fuzzing=False)
    assert parse_utc(hard.after["next_review_at"]) < parse_utc(good.after["next_review_at"])


def test_review_card_again_does_not_enter_relearning():
    current, first = reviewed_row(Rating.Good)
    reviewed_at = parse_utc(first.after["next_review_at"])
    outcome = review(current, Rating.Again, reviewed_at=reviewed_at, enable_fuzzing=False)
    assert outcome.after["fsrs_state"] == int(State.Review)
    assert outcome.after["fsrs_step"] is None
    assert parse_utc(outcome.after["next_review_at"]) - reviewed_at >= timedelta(days=1)


def test_no_short_steps_migration_preserves_data_history_reports_and_stats(tmp_path, monkeypatch):
    client, db = load_app(tmp_path, monkeypatch)
    collection = client.get("/api/dashboard").get_json()["collections"][0]
    learned = make_sentence(client, collection["id"], "learned")
    unlearned = make_sentence(client, collection["id"], "new")
    complete_good(client, learned)

    with db.get_db() as connection:
        connection.execute(
            "DELETE FROM schema_migrations WHERE version=?",
            (db.NO_SHORT_STEPS_MIGRATION,),
        )
        connection.execute(
            """UPDATE sentences
               SET fsrs_state=?,fsrs_step=1,next_review_at=?
               WHERE id=?""",
            (int(State.Learning), "2026-01-01T00:10:00+00:00", learned["id"]),
        )
        counts_before = preserved_counts(connection)
        immutable_before = {
            table: table_rows(connection, table)
            for table in (
                "collections",
                "review_events",
                "attempts",
                "practice_sessions",
                "practice_items",
            )
        }
        content_before = connection.execute(
            """SELECT id,collection_id,chinese,japanese,chunks_json,correct_order_json,
                      furigana_json,created_at,updated_at
               FROM sentences ORDER BY id"""
        ).fetchall()
        unlearned_before = tuple(connection.execute(
            """SELECT fsrs_state,fsrs_step,stability,difficulty,last_review_at,
                      next_review_at,fsrs_version
               FROM sentences WHERE id=?""",
            (unlearned["id"],),
        ).fetchone())

    dashboard_before = client.get("/api/dashboard").get_json()
    today_before = client.get("/api/stats/summary").get_json()["today"]
    backups_before = set((tmp_path / "backups").glob("*.sqlite3"))

    db.init_db(enable_fuzzing=False)

    dashboard_after = client.get("/api/dashboard").get_json()
    today_after = client.get("/api/stats/summary").get_json()["today"]
    with db.get_db() as connection:
        assert preserved_counts(connection) == counts_before
        for table, rows in immutable_before.items():
            assert table_rows(connection, table) == rows
        assert connection.execute(
            """SELECT id,collection_id,chinese,japanese,chunks_json,correct_order_json,
                      furigana_json,created_at,updated_at
               FROM sentences ORDER BY id"""
        ).fetchall() == content_before
        migrated = connection.execute(
            "SELECT * FROM sentences WHERE id=?", (learned["id"],)
        ).fetchone()
        assert migrated["fsrs_state"] == int(State.Review)
        assert migrated["fsrs_step"] is None
        assert migrated["last_review_at"] is not None
        assert tuple(connection.execute(
            """SELECT fsrs_state,fsrs_step,stability,difficulty,last_review_at,
                      next_review_at,fsrs_version
               FROM sentences WHERE id=?""",
            (unlearned["id"],),
        ).fetchone()) == unlearned_before
        assert connection.execute(
            "SELECT COUNT(*) FROM schema_migrations WHERE version=?",
            (db.NO_SHORT_STEPS_MIGRATION,),
        ).fetchone()[0] == 1

    assert dashboard_after["collections"][0]["learned"] == dashboard_before["collections"][0]["learned"] == 1
    assert today_after == today_before

    new_backups = set((tmp_path / "backups").glob("*.sqlite3")) - backups_before
    assert len(new_backups) == 1
    with sqlite3.connect(new_backups.pop()) as backup:
        assert preserved_counts(backup) == counts_before

    with db.get_db() as connection:
        card_before_second_run = tuple(connection.execute(
            """SELECT fsrs_state,fsrs_step,stability,difficulty,last_review_at,
                      next_review_at,fsrs_version
               FROM sentences WHERE id=?""",
            (learned["id"],),
        ).fetchone())
    backup_count = len(list((tmp_path / "backups").glob("*.sqlite3")))
    db.init_db(enable_fuzzing=False)
    with db.get_db() as connection:
        assert tuple(connection.execute(
            """SELECT fsrs_state,fsrs_step,stability,difficulty,last_review_at,
                      next_review_at,fsrs_version
               FROM sentences WHERE id=?""",
            (learned["id"],),
        ).fetchone()) == card_before_second_run
    assert len(list((tmp_path / "backups").glob("*.sqlite3"))) == backup_count


def test_no_short_steps_migration_rolls_back_all_card_updates_on_failure(tmp_path, monkeypatch):
    client, db = load_app(tmp_path, monkeypatch)
    collection_id = client.get("/api/dashboard").get_json()["collections"][0]["id"]
    sentences = [make_sentence(client, collection_id, suffix) for suffix in ("one", "two")]
    for sentence in sentences:
        complete_good(client, sentence)

    with db.get_db() as connection:
        connection.execute(
            "DELETE FROM schema_migrations WHERE version=?",
            (db.NO_SHORT_STEPS_MIGRATION,),
        )
        connection.execute(
            "UPDATE sentences SET fsrs_state=?,fsrs_step=1,next_review_at=?",
            (int(State.Learning), "2026-01-01T00:10:00+00:00"),
        )
        cards_before = [tuple(row) for row in connection.execute(
            """SELECT id,fsrs_state,fsrs_step,stability,difficulty,last_review_at,
                      next_review_at,fsrs_version FROM sentences ORDER BY id"""
        )]
        events_before = table_rows(connection, "review_events")

    real_reschedule = db.reschedule_from_review_events
    calls = 0

    def fail_on_second(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError("simulated reschedule failure")
        return real_reschedule(*args, **kwargs)

    monkeypatch.setattr(db, "reschedule_from_review_events", fail_on_second)
    with pytest.raises(RuntimeError, match="simulated reschedule failure"):
        db.init_db(enable_fuzzing=False)

    with db.get_db() as connection:
        assert [tuple(row) for row in connection.execute(
            """SELECT id,fsrs_state,fsrs_step,stability,difficulty,last_review_at,
                      next_review_at,fsrs_version FROM sentences ORDER BY id"""
        )] == cards_before
        assert table_rows(connection, "review_events") == events_before
        assert connection.execute(
            "SELECT COUNT(*) FROM schema_migrations WHERE version=?",
            (db.NO_SHORT_STEPS_MIGRATION,),
        ).fetchone()[0] == 0


def test_dashboard_due_count_matches_creatable_due_session(tmp_path, monkeypatch):
    client, _ = load_app(tmp_path, monkeypatch)
    collection = client.get("/api/dashboard").get_json()["collections"][0]
    reviewed = make_sentence(client, collection["id"], "scheduled")
    remaining_due = make_sentence(client, collection["id"], "due")
    complete_good(client, reviewed)

    dashboard = client.get("/api/dashboard").get_json()["collections"][0]
    assert dashboard["due"] == 1
    session = client.post(
        "/api/practice/sessions", json={"collectionId": collection["id"]}
    )
    assert session.status_code == 201
    selected = session.get_json()["sentences"]
    assert len(selected) == dashboard["due"]
    assert [sentence["id"] for sentence in selected] == [remaining_due["id"]]
