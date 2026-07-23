import importlib
import json
from datetime import datetime, timezone
from pathlib import Path

FROZEN_NOW = datetime(2026, 1, 1, 15, 30, tzinfo=timezone.utc)
FSRS_FIELDS = (
    "fsrs_state",
    "fsrs_step",
    "stability",
    "difficulty",
    "last_review_at",
    "next_review_at",
    "fsrs_version",
)


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
    import app
    import db

    importlib.reload(db)
    importlib.reload(app)
    client = app.create_app({"TESTING": True}).test_client()
    monkeypatch.setattr(app, "datetime", FrozenDateTime)
    return client, db, app


def default_collection_id(client):
    return client.get("/api/dashboard").get_json()["collections"][0]["id"]


def make_sentence(client, collection_id, suffix):
    chunk_id = f"daily-{suffix}"
    response = client.post("/api/sentences", json={
        "collectionId": collection_id,
        "chinese": f"每日计划 {suffix}",
        "japanese": f"文{suffix}",
        "chunks": [{"id": chunk_id, "text": f"文{suffix}"}],
        "correctOrder": [chunk_id],
    })
    assert response.status_code == 201
    return response.get_json()["sentence"]


def make_old_card(
    db_module,
    sentence_id,
    *,
    stability,
    last_review_at="2025-10-01T00:00:00+00:00",
    next_review_at="2025-12-01T00:00:00+00:00",
):
    with db_module.get_db() as connection:
        connection.execute(
            """UPDATE sentences
               SET fsrs_state=2,fsrs_step=NULL,stability=?,difficulty=5.0,
                   last_review_at=?,next_review_at=?
               WHERE id=?""",
            (stability, last_review_at, next_review_at, sentence_id),
        )


def set_created_at(db_module, sentence_id, created_at):
    with db_module.get_db() as connection:
        connection.execute(
            "UPDATE sentences SET created_at=?,updated_at=? WHERE id=?",
            (created_at, created_at, sentence_id),
        )


def add_review_event(db_module, sentence_id, reviewed_at, *, source="selected"):
    with db_module.get_db() as connection:
        sentence = connection.execute(
            "SELECT * FROM sentences WHERE id=?", (sentence_id,)
        ).fetchone()
        session_id = connection.execute(
            """INSERT INTO practice_sessions(source,sentence_ids_json,total,created_at)
               VALUES(?,?,1,?)""",
            (source, json.dumps([sentence_id]), reviewed_at),
        ).lastrowid
        connection.execute(
            """INSERT INTO review_events(
                 sentence_id,session_id,rating,reviewed_at,duration_ms,is_new,
                 fsrs_state_before,fsrs_state_after,fsrs_step_before,fsrs_step_after,
                 stability_before,stability_after,difficulty_before,difficulty_after,
                 next_review_before,next_review_after,fsrs_version,created_at
               ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                sentence_id,
                session_id,
                3,
                reviewed_at,
                1000,
                int(sentence["last_review_at"] is None),
                sentence["fsrs_state"],
                sentence["fsrs_state"],
                sentence["fsrs_step"],
                sentence["fsrs_step"],
                sentence["stability"],
                sentence["stability"],
                sentence["difficulty"],
                sentence["difficulty"],
                sentence["next_review_at"],
                sentence["next_review_at"],
                sentence["fsrs_version"],
                reviewed_at,
            ),
        )
    return session_id


def fsrs_snapshot(db_module):
    columns = ",".join(("id", *FSRS_FIELDS))
    with db_module.get_db() as connection:
        return [
            tuple(row)
            for row in connection.execute(
                f"SELECT {columns} FROM sentences ORDER BY id"
            )
        ]


def test_daily_plan_setting_defaults_validates_and_is_not_overwritten(
    tmp_path, monkeypatch
):
    client, db, _ = load_app(tmp_path, monkeypatch)

    assert client.get("/api/settings/daily-plan").get_json() == {
        "dailyAutoReviewLimit": 50
    }
    with db.get_db() as connection:
        assert connection.execute(
            "SELECT value FROM settings WHERE key='daily_auto_review_limit'"
        ).fetchone()["value"] == "50"
        connection.execute(
            "DELETE FROM settings WHERE key='daily_auto_review_limit'"
        )
    assert client.get("/api/settings/daily-plan").get_json() == {
        "dailyAutoReviewLimit": 50
    }

    saved = client.put(
        "/api/settings/daily-plan", json={"dailyAutoReviewLimit": 73}
    )
    assert saved.status_code == 200
    assert saved.get_json()["dailyAutoReviewLimit"] == 73
    db.init_db(enable_fuzzing=False)
    assert client.get("/api/settings/daily-plan").get_json() == {
        "dailyAutoReviewLimit": 73
    }

    invalid_values = [None, "50", 1.5, True, 0, -1, 501]
    for value in invalid_values:
        response = client.put(
            "/api/settings/daily-plan", json={"dailyAutoReviewLimit": value}
        )
        assert response.status_code == 400
        assert "1 到 500" in response.get_json()["error"]
    assert client.put("/api/settings/daily-plan", json=[]).status_code == 400
    assert client.get("/api/settings/daily-plan").get_json() == {
        "dailyAutoReviewLimit": 73
    }


def test_auto_queue_orders_old_cards_by_official_retrievability_then_new_cards(
    tmp_path, monkeypatch
):
    client, db, app_module = load_app(tmp_path, monkeypatch)
    collection_id = default_collection_id(client)
    high_risk = make_sentence(client, collection_id, "high-risk")
    lower_risk = make_sentence(client, collection_id, "lower-risk")
    future_old = make_sentence(client, collection_id, "future-old")
    newer_new = make_sentence(client, collection_id, "newer-new")
    older_new = make_sentence(client, collection_id, "older-new")

    make_old_card(
        db,
        high_risk["id"],
        stability=1.0,
        next_review_at="2025-12-31T00:00:00+00:00",
    )
    make_old_card(
        db,
        lower_risk["id"],
        stability=100.0,
        next_review_at="2025-12-01T00:00:00+00:00",
    )
    make_old_card(
        db,
        future_old["id"],
        stability=0.1,
        next_review_at="2099-01-01T00:00:00+00:00",
    )
    set_created_at(db, newer_new["id"], "2025-02-01T00:00:00+00:00")
    set_created_at(db, older_new["id"], "2025-01-01T00:00:00+00:00")

    with db.get_db() as connection:
        risk_row = connection.execute(
            "SELECT * FROM sentences WHERE id=?", (high_risk["id"],)
        ).fetchone()
        safer_row = connection.execute(
            "SELECT * FROM sentences WHERE id=?", (lower_risk["id"],)
        ).fetchone()
        assert app_module.retrievability(
            risk_row, FROZEN_NOW
        ) < app_module.retrievability(safer_row, FROZEN_NOW)

    before = fsrs_snapshot(db)
    response = client.post(
        "/api/practice/sessions",
        json={"collectionId": collection_id, "count": "all"},
    )
    assert response.status_code == 201
    selected = [row["id"] for row in response.get_json()["sentences"]]
    assert selected == [
        high_risk["id"],
        lower_risk["id"],
        older_new["id"],
        newer_new["id"],
    ]
    assert future_old["id"] not in selected
    assert fsrs_snapshot(db) == before
    with db.get_db() as connection:
        assert connection.execute(
            "SELECT COUNT(*) n FROM review_events"
        ).fetchone()["n"] == 0


def test_old_cards_fill_limit_before_new_and_new_cards_use_remaining_space(
    tmp_path, monkeypatch
):
    client, db, _ = load_app(tmp_path, monkeypatch)
    collection_id = default_collection_id(client)
    old_cards = [
        make_sentence(client, collection_id, f"old-{index}") for index in range(3)
    ]
    new_cards = [
        make_sentence(client, collection_id, f"new-{index}") for index in range(2)
    ]
    for index, sentence in enumerate(old_cards):
        make_old_card(
            db,
            sentence["id"],
            stability=1.0,
            next_review_at=f"2025-12-0{index + 1}T00:00:00+00:00",
        )
    for sentence in new_cards:
        set_created_at(db, sentence["id"], "2025-02-01T00:00:00+00:00")

    assert client.put(
        "/api/settings/daily-plan", json={"dailyAutoReviewLimit": 2}
    ).status_code == 200
    old_only = client.post(
        "/api/practice/sessions",
        json={"collectionId": collection_id, "count": "all"},
    ).get_json()["sentences"]
    assert [row["id"] for row in old_only] == [
        old_cards[0]["id"],
        old_cards[1]["id"],
    ]

    assert client.put(
        "/api/settings/daily-plan", json={"dailyAutoReviewLimit": 4}
    ).status_code == 200
    filled = client.post(
        "/api/practice/sessions",
        json={"collectionId": collection_id, "count": "all"},
    ).get_json()["sentences"]
    assert [row["id"] for row in filled] == [
        old_cards[0]["id"],
        old_cards[1]["id"],
        old_cards[2]["id"],
        new_cards[0]["id"],
    ]

    requested_smaller = client.post(
        "/api/practice/sessions",
        json={"collectionId": collection_id, "count": 1},
    ).get_json()["sentences"]
    assert [row["id"] for row in requested_smaller] == [old_cards[0]["id"]]


def test_completed_today_is_distinct_timezone_scoped_and_requires_review_events(
    tmp_path, monkeypatch
):
    client, db, _ = load_app(tmp_path, monkeypatch)
    collection_id = default_collection_id(client)
    repeated = make_sentence(client, collection_id, "repeated")
    outside_day = make_sentence(client, collection_id, "outside")
    unanswered = make_sentence(client, collection_id, "unanswered")

    assert client.put(
        "/api/settings/daily-plan", json={"dailyAutoReviewLimit": 3}
    ).status_code == 200
    assert client.put(
        "/api/settings/timezone", json={"timezone": "Asia/Shanghai"}
    ).status_code == 200

    # Frozen now is 2026-01-01 23:30 in Shanghai. Its natural day starts at
    # 2025-12-31 16:00 UTC and ends at 2026-01-01 16:00 UTC.
    add_review_event(db, repeated["id"], "2025-12-31T16:00:00+00:00")
    add_review_event(db, repeated["id"], "2026-01-01T15:29:00+00:00")
    add_review_event(db, outside_day["id"], "2025-12-31T15:59:59+00:00")

    uncompleted = client.post(
        "/api/practice/sessions", json={"sentenceIds": [unanswered["id"]]}
    )
    assert uncompleted.status_code == 201

    dashboard = client.get("/api/dashboard").get_json()
    collection = next(
        row for row in dashboard["collections"] if row["id"] == collection_id
    )
    assert dashboard["completedToday"] == 1
    assert dashboard["remainingAutoReviewQuota"] == 2
    assert dashboard["availableAutoReviewCount"] == 2
    assert collection["availableAutoReviewCount"] == 2
    assert collection["today"] == 1

    add_review_event(db, outside_day["id"], "2026-01-01T15:59:59+00:00")
    updated = client.get("/api/dashboard").get_json()
    assert updated["completedToday"] == 2
    assert updated["remainingAutoReviewQuota"] == 1
    assert updated["availableAutoReviewCount"] == 1

    assert client.put(
        "/api/settings/daily-plan", json={"dailyAutoReviewLimit": 1}
    ).status_code == 200
    over_limit = client.get("/api/dashboard").get_json()
    assert over_limit["completedToday"] == 2
    assert over_limit["remainingAutoReviewQuota"] == 0


def test_zero_quota_blocks_only_due_sessions(
    tmp_path, monkeypatch
):
    client, db, _ = load_app(tmp_path, monkeypatch)
    collection_id = default_collection_id(client)
    completed = make_sentence(client, collection_id, "completed")
    make_sentence(client, collection_id, "random")
    selected_target = make_sentence(client, collection_id, "selected")
    retry_target = make_sentence(client, collection_id, "retry")
    assert client.put(
        "/api/settings/daily-plan", json={"dailyAutoReviewLimit": 1}
    ).status_code == 200
    add_review_event(db, completed["id"], "2026-01-01T15:00:00+00:00")

    with db.get_db() as connection:
        sessions_before = connection.execute(
            "SELECT COUNT(*) n FROM practice_sessions"
        ).fetchone()["n"]
    blocked = client.post(
        "/api/practice/sessions",
        json={"collectionId": collection_id, "count": "all"},
    )
    assert blocked.status_code == 409
    assert "今日自动复习计划已完成" in blocked.get_json()["error"]
    with db.get_db() as connection:
        assert connection.execute(
            "SELECT COUNT(*) n FROM practice_sessions"
        ).fetchone()["n"] == sessions_before

    random_round = client.post(
        "/api/practice/sessions",
        json={"scope": "collection", "collectionId": collection_id, "count": 1},
    )
    assert random_round.status_code == 201
    selected_round = client.post(
        "/api/practice/sessions", json={"sentenceIds": [selected_target["id"]]}
    )
    assert selected_round.status_code == 201

    report_source = client.post(
        "/api/practice/sessions", json={"sentenceIds": [retry_target["id"]]}
    ).get_json()
    report_id = report_source["sessionId"]
    completed_report = client.post(
        f"/api/practice/sessions/{report_id}/complete",
        json={"confirmUnanswered": True},
    )
    assert completed_report.status_code == 200
    retry_round = client.post(
        "/api/practice/sessions",
        json={"scope": "report_retry", "reportId": report_id, "count": "all"},
    )
    assert retry_round.status_code == 201
    assert retry_round.get_json()["sentences"][0]["id"] == retry_target["id"]

    with db.get_db() as connection:
        sources = {
            row["source"]
            for row in connection.execute(
                "SELECT source FROM practice_sessions WHERE id>?",
                (sessions_before,),
            )
        }
    assert {"collection", "selected", "report_retry"} <= sources


def test_dashboard_keeps_true_due_count_and_backend_caps_stale_picker_input(
    tmp_path, monkeypatch
):
    client, db, _ = load_app(tmp_path, monkeypatch)
    collection_id = default_collection_id(client)
    sentences = [
        make_sentence(client, collection_id, f"due-{index}") for index in range(5)
    ]
    assert client.put(
        "/api/settings/daily-plan", json={"dailyAutoReviewLimit": 2}
    ).status_code == 200

    before = fsrs_snapshot(db)
    dashboard = client.get("/api/dashboard").get_json()
    collection = next(
        row for row in dashboard["collections"] if row["id"] == collection_id
    )
    assert dashboard["due"] == 5
    assert collection["due"] == 5
    assert dashboard["remainingAutoReviewQuota"] == 2
    assert dashboard["availableAutoReviewCount"] == 2
    assert collection["availableAutoReviewCount"] == 2
    assert fsrs_snapshot(db) == before

    oversized = client.post(
        "/api/practice/sessions",
        json={"collectionId": collection_id, "count": 99},
    )
    assert oversized.status_code == 201
    assert len(oversized.get_json()["sentences"]) == 2
    assert "只有 2 句" in oversized.get_json()["notice"]
    assert fsrs_snapshot(db) == before

    for invalid in (0, -1, 1.5, True, "nope"):
        response = client.post(
            "/api/practice/sessions",
            json={"collectionId": collection_id, "count": invalid},
        )
        assert response.status_code == 400
        assert "正整数" in response.get_json()["error"]

    all_round = client.post(
        "/api/practice/sessions",
        json={"collectionId": collection_id, "count": "all"},
    )
    assert all_round.status_code == 201
    assert len(all_round.get_json()["sentences"]) == 2
    assert {row["id"] for row in all_round.get_json()["sentences"]} <= {
        sentence["id"] for sentence in sentences
    }


def test_frontend_uses_dashboard_auto_limit_for_home_picker_and_settings():
    static_dir = Path(__file__).parents[1] / "static"
    source = (static_dir / "app.js").read_text(encoding="utf-8")
    styles = (static_dir / "styles.css").read_text(encoding="utf-8")

    assert "active?.availableAutoReviewCount || 0" in source
    assert "max: due" not in source
    assert 'max="${inputMax}"' in source
    assert "今日还可自动练习" in source
    assert "今日自动复习计划已完成。仍可进入句集进行专项练习" in source
    assert "api('/api/settings/daily-plan')" in source
    assert 'id="daily-plan-form"' in source
    assert 'min="1" max="500" step="1"' in source
    assert "scope: 'collection'" in source
    assert "scope: 'report_retry'" in source
    assert "startPractice({sentenceIds:ids})" in source
    assert ".count-option,.custom-count input{min-height:48px}" in styles
    assert ".hero-bottom{display:grid;grid-template-columns:repeat(2,minmax(0,1fr))" in styles
    assert ".hero-bottom .auto-review-metric{grid-column:1/-1;grid-row:2}" in styles
