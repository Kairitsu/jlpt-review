import importlib
import json
import sqlite3
from datetime import datetime, timezone

import pytest
from fsrs import Rating, State


def load_app(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("APP_SECRET", "test-secret")
    monkeypatch.setenv("SESSION_COOKIE_SECURE", "false")
    monkeypatch.setenv("TRUST_PROXY_COUNT", "0")
    import db, app
    importlib.reload(db)
    importlib.reload(app)
    return app.create_app({"TESTING": True}).test_client(), db


def make_sentence(client, collection_id, suffix=""):
    chunks = [{"id": f"a{suffix}", "text": f"文{suffix}"}]
    response = client.post("/api/sentences", json={
        "collectionId": collection_id,
        "chinese": f"句子{suffix}",
        "japanese": f"文{suffix}",
        "chunks": chunks,
        "correctOrder": [f"a{suffix}"],
    })
    assert response.status_code == 201
    return response.get_json()["sentence"]


def start(client, sentence, retry_wrong=False):
    response = client.post("/api/practice/sessions", json={
        "sentenceIds": [sentence["id"]], "retryWrong": retry_wrong,
    })
    assert response.status_code == 201
    return response.get_json()["sessionId"]


def check(client, session_id, sentence, correct, duration_ms=1000):
    return client.post(f"/api/practice/sessions/{session_id}/attempts", json={
        "sentenceId": sentence["id"],
        "action": "check",
        "answerOrder": sentence["correctOrder"] if correct else [],
        "durationMs": duration_ms,
    })


def finish(client, session_id, sentence, easy=False):
    return client.post(
        f"/api/practice/sessions/{session_id}/sentences/{sentence['id']}/complete",
        json={"easy": easy},
    )


def stored_card(db, sentence_id):
    with db.get_db() as connection:
        return dict(connection.execute("SELECT * FROM sentences WHERE id=?", (sentence_id,)).fetchone())


def test_new_sentence_is_immediately_due_fsrs_new_card(tmp_path, monkeypatch):
    client, db = load_app(tmp_path, monkeypatch)
    collection = client.get("/api/dashboard").get_json()["collections"][0]["id"]
    sentence = make_sentence(client, collection, "new")
    row = stored_card(db, sentence["id"])
    assert row["fsrs_state"] == int(State.Learning)
    assert row["fsrs_step"] == 0
    assert row["stability"] is None and row["difficulty"] is None
    assert row["last_review_at"] is None
    assert row["fsrs_version"] == "6.3.1"
    assert datetime.fromisoformat(row["next_review_at"]) <= datetime.now(timezone.utc)
    due = client.post("/api/practice/sessions", json={"collectionId": collection})
    assert sentence["id"] in [item["id"] for item in due.get_json()["sentences"]]


def test_check_only_appends_raw_attempt_without_changing_fsrs(tmp_path, monkeypatch):
    client, db = load_app(tmp_path, monkeypatch)
    collection = client.get("/api/dashboard").get_json()["collections"][0]["id"]
    sentence = make_sentence(client, collection, "raw")
    session_id = start(client, sentence)
    before = stored_card(db, sentence["id"])
    assert check(client, session_id, sentence, False).get_json()["status"] == "wrong"
    assert check(client, session_id, sentence, True).get_json()["status"] == "correct"
    after = stored_card(db, sentence["id"])
    for field in ("fsrs_state", "fsrs_step", "stability", "difficulty", "last_review_at", "next_review_at"):
        assert after[field] == before[field]
    with db.get_db() as connection:
        assert connection.execute("SELECT COUNT(*) n FROM attempts").fetchone()["n"] == 2
        assert connection.execute("SELECT COUNT(*) n FROM review_events").fetchone()["n"] == 0


@pytest.mark.parametrize(
    "checks,easy,expected",
    [
        ([False], False, "again"),
        ([False, True], False, "hard"),
        ([True], False, "good"),
        ([True], True, "easy"),
    ],
)
def test_final_rating_updates_once(checks, easy, expected, tmp_path, monkeypatch):
    client, db = load_app(tmp_path, monkeypatch)
    collection = client.get("/api/dashboard").get_json()["collections"][0]["id"]
    sentence = make_sentence(client, collection, expected)
    session_id = start(client, sentence, retry_wrong=True)
    for correct in checks:
        assert check(client, session_id, sentence, correct).status_code == 200
    completed = finish(client, session_id, sentence, easy=easy)
    assert completed.status_code == 200
    assert completed.get_json()["rating"] == expected
    row = stored_card(db, sentence["id"])
    assert row["stability"] is not None and row["difficulty"] is not None
    with db.get_db() as connection:
        event = connection.execute("SELECT * FROM review_events").fetchone()
        assert event["rating"] == int(getattr(Rating, expected.capitalize()))
        assert event["next_review_after"] == row["next_review_at"]
        assert event["fsrs_version"] == "6.3.1"
        assert event["duration_ms"] == len(checks) * 1000

    duplicate = finish(client, session_id, sentence, easy=not easy)
    assert duplicate.status_code == 200 and duplicate.get_json()["duplicate"] is True
    assert stored_card(db, sentence["id"])["next_review_at"] == row["next_review_at"]
    with db.get_db() as connection:
        assert connection.execute("SELECT COUNT(*) n FROM review_events").fetchone()["n"] == 1


def test_skip_finalizes_without_fsrs_change(tmp_path, monkeypatch):
    client, db = load_app(tmp_path, monkeypatch)
    collection = client.get("/api/dashboard").get_json()["collections"][0]["id"]
    sentence = make_sentence(client, collection, "skip")
    session_id = start(client, sentence)
    before = stored_card(db, sentence["id"])
    response = client.post(f"/api/practice/sessions/{session_id}/attempts", json={
        "sentenceId": sentence["id"], "action": "skip", "answerOrder": [],
    })
    assert response.status_code == 200
    completed = finish(client, session_id, sentence)
    assert completed.get_json()["rating"] is None
    assert stored_card(db, sentence["id"])["next_review_at"] == before["next_review_at"]
    with db.get_db() as connection:
        assert connection.execute("SELECT COUNT(*) n FROM review_events").fetchone()["n"] == 0


def test_database_unique_constraint_and_delete_cascade(tmp_path, monkeypatch):
    client, db = load_app(tmp_path, monkeypatch)
    collection = client.get("/api/dashboard").get_json()["collections"][0]["id"]
    sentence = make_sentence(client, collection, "delete")
    session_id = start(client, sentence)
    check(client, session_id, sentence, True)
    finish(client, session_id, sentence)
    with db.get_db() as connection:
        event = dict(connection.execute("SELECT * FROM review_events").fetchone())
        columns = [key for key in event if key != "id"]
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                f"INSERT INTO review_events({','.join(columns)}) VALUES({','.join('?' for _ in columns)})",
                [event[key] for key in columns],
            )
    assert client.delete(f"/api/sentences/{sentence['id']}").status_code == 200
    with db.get_db() as connection:
        assert connection.execute("SELECT COUNT(*) n FROM review_events").fetchone()["n"] == 0
        assert connection.execute("SELECT COUNT(*) n FROM attempts").fetchone()["n"] == 0
        assert connection.execute("SELECT COUNT(*) n FROM practice_items").fetchone()["n"] == 0


def create_legacy_database(path):
    stamp = "2025-01-01T00:00:00+00:00"
    with sqlite3.connect(path) as db:
        db.executescript("""
          CREATE TABLE settings(key TEXT PRIMARY KEY,value TEXT NOT NULL);
          CREATE TABLE collections(id INTEGER PRIMARY KEY,name TEXT,created_at TEXT,updated_at TEXT);
          CREATE TABLE sentences(
            id INTEGER PRIMARY KEY, collection_id INTEGER, chinese TEXT, japanese TEXT,
            chunks_json TEXT, correct_order_json TEXT, furigana_json TEXT,
            study_count INTEGER, correct_count INTEGER, wrong_count INTEGER, skip_count INTEGER,
            correct_streak INTEGER, stability REAL, review_count INTEGER, lapse_count INTEGER,
            next_review_at TEXT, created_at TEXT, updated_at TEXT, last_practiced_at TEXT
          );
          CREATE TABLE practice_sessions(id INTEGER PRIMARY KEY,source TEXT,sentence_ids_json TEXT,total INTEGER,correct INTEGER,wrong INTEGER,skipped INTEGER,completed_at TEXT,created_at TEXT);
          CREATE TABLE attempts(id INTEGER PRIMARY KEY,session_id INTEGER,sentence_id INTEGER,status TEXT,answer_order_json TEXT,sentence_snapshot_json TEXT,created_at TEXT);
          CREATE TABLE review_events(id INTEGER PRIMARY KEY,sentence_id INTEGER,session_id INTEGER,reviewed_at TEXT,result TEXT,created_at TEXT);
        """)
        db.execute("INSERT INTO settings VALUES('scheduler_mode','fixed')")
        db.execute("INSERT INTO collections VALUES(1,'旧句集',?,?)", (stamp, stamp))
        db.execute("""INSERT INTO sentences VALUES(
          1,1,'保留中文','保留日本語','[{"id":"a","text":"保留日本語"}]','["a"]','[]',
          9,8,7,6,5,99,4,3,'2099-01-01T00:00:00+00:00',?,?,?)""", (stamp, stamp, stamp))
        db.execute("INSERT INTO practice_sessions VALUES(1,'due','[1]',1,1,0,0,?,?)", (stamp, stamp))
        db.execute("INSERT INTO attempts VALUES(1,1,1,'correct','[]','{}',?)", (stamp,))
        db.execute("INSERT INTO review_events VALUES(1,1,1,?,'known',?)", (stamp, stamp))


def test_one_time_migration_preserves_content_resets_progress_and_is_idempotent(tmp_path, monkeypatch):
    db_path = tmp_path / "japanese_sentence_review.sqlite3"
    create_legacy_database(db_path)
    client, db = load_app(tmp_path, monkeypatch)
    sentence = client.get("/api/sentences/1").get_json()["sentence"]
    assert sentence["chinese"] == "保留中文" and sentence["japanese"] == "保留日本語"
    assert sentence["chunks"][0]["text"] == "保留日本語"
    assert sentence["fsrs_state"] == int(State.Learning)
    assert sentence["stability"] is None and sentence["last_review_at"] is None
    with db.get_db() as connection:
        assert connection.execute("SELECT COUNT(*) n FROM attempts").fetchone()["n"] == 0
        assert connection.execute("SELECT COUNT(*) n FROM review_events").fetchone()["n"] == 0
        assert connection.execute("SELECT COUNT(*) n FROM practice_sessions").fetchone()["n"] == 0
        assert connection.execute("SELECT value FROM settings WHERE key='scheduler_mode'").fetchone() is None
        assert connection.execute("SELECT COUNT(*) n FROM schema_migrations WHERE version='fsrs_v1_reset'").fetchone()["n"] == 1

    session_id = start(client, sentence)
    check(client, session_id, sentence, True)
    finish(client, session_id, sentence)
    db.init_db()
    db.init_db()
    with db.get_db() as connection:
        assert connection.execute("SELECT COUNT(*) n FROM review_events").fetchone()["n"] == 1
        assert connection.execute("SELECT COUNT(*) n FROM attempts").fetchone()["n"] == 1


def test_timezone_only_changes_natural_day_not_fsrs_utc(tmp_path, monkeypatch):
    client, db = load_app(tmp_path, monkeypatch)
    collection = client.get("/api/dashboard").get_json()["collections"][0]["id"]
    sentence = make_sentence(client, collection, "tz")
    assert client.put("/api/settings/timezone", json={"timezone": "Pacific/Kiritimati"}).status_code == 200
    session_id = start(client, sentence)
    check(client, session_id, sentence, True)
    finish(client, session_id, sentence)
    row = stored_card(db, sentence["id"])
    assert row["last_review_at"].endswith("+00:00")
    assert row["next_review_at"].endswith("+00:00")
    summary = client.get("/api/stats/summary").get_json()
    assert summary["today"]["learned"] == 1


def test_fsrs_stats_and_read_only_settings(tmp_path, monkeypatch):
    client, _ = load_app(tmp_path, monkeypatch)
    collection = client.get("/api/dashboard").get_json()["collections"][0]["id"]
    sentence = make_sentence(client, collection, "stats")
    session_id = start(client, sentence)
    check(client, session_id, sentence, False)
    check(client, session_id, sentence, True)
    finish(client, session_id, sentence)
    data = client.get("/api/stats/summary").get_json()
    assert data["today"]["learned"] == 1
    assert data["today"]["ratings"]["hard"] == 1
    assert set(data["forecast"]) == {"days7", "days30", "days90"}
    assert data["stabilityDistribution"] and data["difficultyDistribution"]
    assert data["retentionPct"] is not None
    settings = client.get("/api/settings/fsrs").get_json()
    assert settings == {"system": "FSRS", "desiredRetention": 0.9, "maximumIntervalDays": 36500, "version": "6.3.1"}
    assert client.get("/api/settings/scheduler").status_code == 404
    assert client.put("/api/settings/scheduler", json={"mode": "fixed"}).status_code == 404
