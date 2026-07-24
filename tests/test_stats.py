import importlib
import json
import sqlite3
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fsrs import Rating, State


FROZEN_NOW = datetime(2026, 1, 2, 15, 30, tzinfo=timezone.utc)


class FrozenDateTime(datetime):
    @classmethod
    def now(cls, tz=None):
        if tz is None:
            return FROZEN_NOW.replace(tzinfo=None)
        return FROZEN_NOW.astimezone(tz)


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
        "attemptId": str(uuid.uuid4()),
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


def add_review_event(
    db_module,
    sentence_id,
    *,
    reviewed_at,
    rating=Rating.Good,
    duration_ms=0,
    is_new=False,
):
    with db_module.get_db() as connection:
        card = connection.execute(
            """SELECT * FROM practice_cards
               WHERE sentence_id=? AND card_type='sentence_order' AND active=1""",
            (sentence_id,),
        ).fetchone()
        session_id = connection.execute(
            """INSERT INTO practice_sessions(source,sentence_ids_json,total,created_at)
               VALUES('selected',?,1,?)""",
            (json.dumps([sentence_id]), reviewed_at),
        ).lastrowid
        connection.execute(
            """INSERT INTO review_events(
                 card_id,sentence_id,session_id,rating,reviewed_at,duration_ms,is_new,
                 fsrs_state_before,fsrs_state_after,fsrs_step_before,fsrs_step_after,
                 stability_before,stability_after,difficulty_before,difficulty_after,
                 next_review_before,next_review_after,fsrs_version,created_at
               ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                card["id"], sentence_id, session_id, int(rating), reviewed_at,
                duration_ms, int(is_new),
                card["fsrs_state"], card["fsrs_state"],
                card["fsrs_step"], card["fsrs_step"],
                card["stability"], card["stability"],
                card["difficulty"], card["difficulty"],
                card["next_review_at"], card["next_review_at"],
                card["fsrs_version"], reviewed_at,
            ),
        )


def freeze_stats_clock(monkeypatch):
    import app
    monkeypatch.setattr(app, "datetime", FrozenDateTime)


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
    "checks,legacy_easy,expected",
    [
        ([False], False, "again"),
        ([False, True], False, "hard"),
        ([True], False, "good"),
        ([True], True, "good"),
    ],
)
def test_final_rating_updates_once(checks, legacy_easy, expected, tmp_path, monkeypatch):
    client, db = load_app(tmp_path, monkeypatch)
    collection = client.get("/api/dashboard").get_json()["collections"][0]["id"]
    sentence = make_sentence(client, collection, expected)
    session_id = start(client, sentence, retry_wrong=True)
    for correct in checks:
        assert check(client, session_id, sentence, correct).status_code == 200
    completed = finish(client, session_id, sentence, easy=legacy_easy)
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

    duplicate = finish(client, session_id, sentence, easy=not legacy_easy)
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
        "attemptId": str(uuid.uuid4()),
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
    today = next(day for day in summary["timeline"] if day["isToday"])
    assert today["actual"]["newCount"] == 1


def test_learning_overview_replaces_old_stats_fields_and_keeps_fsrs_settings(
    tmp_path, monkeypatch
):
    client, _ = load_app(tmp_path, monkeypatch)
    collection = client.get("/api/dashboard").get_json()["collections"][0]["id"]
    sentence = make_sentence(client, collection, "stats")
    session_id = start(client, sentence)
    check(client, session_id, sentence, False)
    check(client, session_id, sentence, True)
    finish(client, session_id, sentence)
    data = client.get("/api/stats/summary").get_json()
    today = next(day for day in data["timeline"] if day["isToday"])
    assert today["actual"]["newCount"] == 1
    assert next(
        group for group in today["actual"]["ratings"]["groups"]
        if group["label"] == "模糊"
    )["count"] == 1
    assert set(data) == {
        "generatedAt", "timezone", "timeline", "upcomingDue", "memoryMastery",
        "kanjiReading",
    }
    for removed in (
        "forecast", "retentionPct", "reviewedCards",
        "stabilityDistribution", "difficultyDistribution", "fsrs", "today",
    ):
        assert removed not in data
    settings = client.get("/api/settings/fsrs").get_json()
    assert settings == {"system": "FSRS", "desiredRetention": 0.98, "maximumIntervalDays": 36500, "version": "6.3.1"}
    assert client.get("/api/settings/scheduler").status_code == 404
    assert client.put("/api/settings/scheduler", json={"mode": "fixed"}).status_code == 404


def test_history_and_upcoming_due_use_user_timezone_and_half_open_day_ranges(
    tmp_path, monkeypatch
):
    client, db = load_app(tmp_path, monkeypatch)
    freeze_stats_clock(monkeypatch)
    collection = client.get("/api/dashboard").get_json()["collections"][0]["id"]
    sentences = [make_sentence(client, collection, f"timeline-{index}") for index in range(8)]
    assert client.put(
        "/api/settings/timezone", json={"timezone": "Asia/Shanghai"}
    ).status_code == 200

    # Frozen time is 23:30 on Jan 2 in Shanghai. Both learning events and due
    # cards must follow Shanghai's [00:00, 24:00) natural days.
    add_review_event(
        db, sentences[0]["id"], reviewed_at="2025-12-28T15:59:59+00:00",
        rating=Rating.Again, duration_ms=50, is_new=False,
    )
    add_review_event(
        db, sentences[0]["id"], reviewed_at="2025-12-28T16:00:00+00:00",
        rating=Rating.Again, duration_ms=100, is_new=False,
    )
    add_review_event(
        db, sentences[0]["id"], reviewed_at="2026-01-01T15:59:59+00:00",
        rating=Rating.Again, duration_ms=500, is_new=True,
    )
    add_review_event(
        db, sentences[0]["id"], reviewed_at="2026-01-01T16:00:00+00:00",
        rating=Rating.Hard, duration_ms=30_500, is_new=True,
    )
    add_review_event(
        db, sentences[0]["id"], reviewed_at="2026-01-02T15:59:59+00:00",
        rating=Rating.Good, duration_ms=60_000, is_new=False,
    )
    add_review_event(
        db, sentences[0]["id"], reviewed_at="2026-01-02T16:00:00+00:00",
        rating=Rating.Easy, duration_ms=70_000, is_new=False,
    )
    with db.get_db() as connection:
        schedules = [
            ("2025-12-20T00:00:00+00:00", sentences[0]["id"]),
            (FROZEN_NOW.isoformat(timespec="seconds"), sentences[1]["id"]),
            ("2026-01-02T16:00:00+00:00", sentences[2]["id"]),
            ("2026-01-03T15:59:59+00:00", sentences[3]["id"]),
            ("2026-01-03T16:00:00+00:00", sentences[4]["id"]),
            ("2026-01-04T16:00:00+00:00", sentences[5]["id"]),
            ("2026-01-05T15:59:59+00:00", sentences[6]["id"]),
            ("2026-01-05T16:00:00+00:00", sentences[7]["id"]),
        ]
        connection.executemany(
            "UPDATE sentences SET next_review_at=? WHERE id=?",
            schedules,
        )
        connection.executemany(
            """UPDATE practice_cards SET next_review_at=?
               WHERE sentence_id=? AND card_type='sentence_order' AND active=1""",
            schedules,
        )

    data = client.get("/api/stats/summary").get_json()
    timeline = data["timeline"]
    assert data["generatedAt"] == FROZEN_NOW.isoformat(timespec="seconds")
    assert data["timezone"] == {"name": "Asia/Shanghai", "source": "user"}
    assert [day["date"] for day in timeline] == [
        "2025-12-29", "2025-12-30", "2025-12-31", "2026-01-01", "2026-01-02",
    ]
    assert [day["relativeLabel"] for day in timeline] == [
        "4天前", "3天前", "前天", "昨天", "今天",
    ]
    assert [day["isToday"] for day in timeline] == [False, False, False, False, True]
    assert all(
        datetime.fromisoformat(right["date"]).date()
        - datetime.fromisoformat(left["date"]).date() == timedelta(days=1)
        for left, right in zip(timeline, timeline[1:])
    )
    assert timeline[0]["actual"]["completedCount"] == 1
    assert timeline[0]["actual"]["durationMs"] == 100
    assert timeline[1]["actual"]["completedCount"] == 0
    assert timeline[2]["actual"]["completedCount"] == 0
    yesterday = timeline[3]["actual"]
    assert yesterday["completedCount"] == yesterday["newCount"] + yesterday["reviewCount"] == 1
    today = timeline[4]["actual"]
    assert today["completedCount"] == today["newCount"] + today["reviewCount"] == 2
    assert today["durationMs"] == 90_500
    assert today["ratings"]["validCount"] == 2
    assert sum(group["count"] for group in today["ratings"]["groups"]) == 2
    assert {group["label"]: group["percentage"] for group in today["ratings"]["groups"]} == {
        "忘记": 0.0, "模糊": 50.0, "认识": 50.0, "轻松掌握": 0.0,
    }
    assert all("due" not in day for day in timeline)

    assert data["upcomingDue"] == [
        {
            "date": "2026-01-03", "monthDay": "1月3日", "weekday": "星期六",
            "relativeLabel": "明天", "count": 2,
        },
        {
            "date": "2026-01-04", "monthDay": "1月4日", "weekday": "星期日",
            "relativeLabel": "后天", "count": 1,
        },
        {
            "date": "2026-01-05", "monthDay": "1月5日", "weekday": "星期一",
            "relativeLabel": "3天后", "count": 2,
        },
    ]
    assert sum(day["count"] for day in data["upcomingDue"]) == 5


def test_empty_stats_keep_five_history_days_and_three_upcoming_rows(
    tmp_path, monkeypatch
):
    client, _ = load_app(tmp_path, monkeypatch)
    freeze_stats_clock(monkeypatch)
    data = client.get("/api/stats/summary").get_json()
    assert len(data["timeline"]) == 5
    for day in data["timeline"]:
        assert day["actual"]["completedCount"] == 0
        assert day["actual"]["durationMs"] == 0
        assert day["actual"]["ratings"]["validCount"] == 0
        assert all(
            group["percentage"] is None
            for group in day["actual"]["ratings"]["groups"]
        )
    assert len(data["upcomingDue"]) == 3
    assert [day["relativeLabel"] for day in data["upcomingDue"]] == [
        "明天", "后天", "3天后",
    ]
    assert [day["count"] for day in data["upcomingDue"]] == [0, 0, 0]
    assert data["memoryMastery"]["effectiveSentenceCount"] == 0
    assert data["memoryMastery"]["untrackedSentenceCount"] == 0


def test_rating_percentages_exclude_skipped_unanswered_and_items_without_events(
    tmp_path, monkeypatch
):
    client, db = load_app(tmp_path, monkeypatch)
    collection = client.get("/api/dashboard").get_json()["collections"][0]["id"]
    rated = make_sentence(client, collection, "rated-only")
    skipped = make_sentence(client, collection, "skipped")
    unanswered = make_sentence(client, collection, "unanswered")

    rated_session = start(client, rated)
    assert check(client, rated_session, rated, True, duration_ms=1_250).status_code == 200
    assert finish(client, rated_session, rated).status_code == 200

    skipped_session = start(client, skipped)
    assert client.post(
        f"/api/practice/sessions/{skipped_session}/attempts",
        json={
            "attemptId": str(uuid.uuid4()),
            "sentenceId": skipped["id"],
            "action": "skip",
            "answerOrder": [],
        },
    ).status_code == 200
    assert finish(client, skipped_session, skipped).status_code == 200

    unanswered_session = start(client, unanswered)
    completed = client.post(
        f"/api/practice/sessions/{unanswered_session}/complete",
        json={
            "confirmUnanswered": True,
            "draftAnswers": [{"sentenceId": unanswered["id"], "answerOrder": []}],
        },
    )
    assert completed.status_code == 200

    with db.get_db() as connection:
        assert connection.execute("SELECT COUNT(*) n FROM review_events").fetchone()["n"] == 1
    today = next(
        day for day in client.get("/api/stats/summary").get_json()["timeline"]
        if day["isToday"]
    )["actual"]
    assert today["completedCount"] == 1
    assert today["durationMs"] == 1_250
    assert today["ratings"]["validCount"] == 1
    groups = {group["key"]: group for group in today["ratings"]["groups"]}
    assert groups["recognized"]["count"] == 1
    assert groups["recognized"]["percentage"] == 100.0
    assert sum(group["count"] for group in groups.values()) == 1


def test_memory_mastery_uses_official_service_boundary_and_exclusive_ranges(
    tmp_path, monkeypatch
):
    client, db = load_app(tmp_path, monkeypatch)
    freeze_stats_clock(monkeypatch)
    collection = client.get("/api/dashboard").get_json()["collections"][0]["id"]
    tracked = [make_sentence(client, collection, f"memory-{index}") for index in range(6)]
    untracked = make_sentence(client, collection, "memory-untracked")
    with db.get_db() as connection:
        connection.executemany(
            """UPDATE sentences
               SET last_review_at=?,stability=1.0,difficulty=5.0 WHERE id=?""",
            [("2026-01-01T00:00:00+00:00", item["id"]) for item in tracked],
        )
        connection.executemany(
            """UPDATE practice_cards
               SET last_review_at=?,stability=1.0,difficulty=5.0
               WHERE sentence_id=? AND card_type='sentence_order' AND active=1""",
            [("2026-01-01T00:00:00+00:00", item["id"]) for item in tracked],
        )

    with db.get_db() as connection:
        tracked_card_ids = [
            connection.execute(
                """SELECT id FROM practice_cards
                   WHERE sentence_id=? AND card_type='sentence_order' AND active=1""",
                (item["id"],),
            ).fetchone()["id"]
            for item in tracked
        ]
        untracked_card_id = connection.execute(
            """SELECT id FROM practice_cards
               WHERE sentence_id=? AND card_type='sentence_order' AND active=1""",
            (untracked["id"],),
        ).fetchone()["id"]
    probabilities = dict(zip(
        tracked_card_ids,
        [0.95, 0.949, 0.90, 0.899, 0.80, 0.799],
        strict=True,
    ))
    calls = []
    import app

    def official_probability(row, now):
        calls.append((row["id"], now))
        return probabilities[row["id"]]

    monkeypatch.setattr(app, "retrievability", official_probability)
    mastery = client.get("/api/stats/summary").get_json()["memoryMastery"]
    groups = {group["key"]: group for group in mastery["groups"]}
    assert {sentence_id for sentence_id, _ in calls} == set(probabilities)
    assert all(now == FROZEN_NOW for _, now in calls)
    assert mastery["totalSentenceCount"] == 7
    assert mastery["effectiveSentenceCount"] == 6
    assert mastery["untrackedSentenceCount"] == 1
    assert [groups[key]["count"] for key in ("veryStrong", "strong", "atRisk", "priority")] == [1, 2, 2, 1]
    assert sum(groups[key]["count"] for key in ("veryStrong", "strong", "atRisk", "priority")) == 6
    assert [groups[key]["percentage"] for key in ("veryStrong", "strong", "atRisk", "priority")] == [16.7, 33.3, 33.3, 16.7]
    assert groups["untracked"] == {
        "key": "untracked",
        "label": "尚未形成有效复习记录",
        "count": 1,
        "percentage": None,
        "status": "尚无有效学习记录",
        "includedInPercentage": False,
    }
    assert untracked_card_id not in {card_id for card_id, _ in calls}


def test_stats_frontend_has_two_history_views_upcoming_table_and_dynamic_today():
    root = Path(__file__).parents[1]
    html = (root / "static" / "index.html").read_text()
    source = (root / "static" / "stats.js").read_text()
    styles = (root / "static" / "styles.css").read_text()
    assert '/static/vendor/chart.umd.min.js' in html
    assert "destroyStatsCharts" in source
    assert ".destroy()" in source
    assert "calendarView: 'performance'" in source
    assert "学习表现" in source and "学习时长" in source
    assert "stats-view" in source
    assert "stats-series" in source
    assert "restore" in source
    assert "aria-pressed" in source
    assert "keydown" in source
    assert "学习数量" not in source
    assert "quantity" not in source
    assert "新学句数" not in source and "复习句数" not in source and "到期句数" not in source
    assert "stats-calendar-summary" not in source
    assert "stats-day-summary" not in source and "stats-day-summary" not in styles
    assert 'aria-describedby="stats-calendar-summary"' not in source
    assert "stats-memory-series" not in source
    assert "stats-restore-memory" not in source
    assert "hiddenMasteryGroups" not in source
    assert "masteryControlsHtml" not in source
    assert "chartWrap.classList.toggle('hidden', !hasSentences)" in source
    assert "<table" in source and "stats-upcoming-table" in source
    assert "未来三天预计到期" in source
    assert "findIndex(day => day.isToday)" in source
    assert "getPixelForValue(todayIndex)" in source
    assert "context.index === todayIndex" in source
    assert "getPixelForValue(2)" not in source
    assert "context.index === 2" not in source
