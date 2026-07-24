import importlib
import json
import uuid
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
    import fsrs_service

    importlib.reload(db)
    importlib.reload(app)
    client = app.create_app({"TESTING": True}).test_client()
    monkeypatch.setattr(app, "datetime", FrozenDateTime)
    monkeypatch.setattr(fsrs_service, "utc_now", lambda: FROZEN_NOW)
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
        connection.execute(
            """UPDATE practice_cards
               SET fsrs_state=2,fsrs_step=NULL,stability=?,difficulty=5.0,
                   last_review_at=?,next_review_at=?
               WHERE sentence_id=? AND card_type='sentence_order' AND active=1""",
            (stability, last_review_at, next_review_at, sentence_id),
        )


def set_created_at(db_module, sentence_id, created_at):
    with db_module.get_db() as connection:
        connection.execute(
            "UPDATE sentences SET created_at=?,updated_at=? WHERE id=?",
            (created_at, created_at, sentence_id),
        )
        connection.execute(
            """UPDATE practice_cards SET created_at=?,updated_at=?
               WHERE sentence_id=? AND card_type='sentence_order' AND active=1""",
            (created_at, created_at, sentence_id),
        )


def add_review_event(db_module, sentence_id, reviewed_at, *, source="selected"):
    with db_module.get_db() as connection:
        card = connection.execute(
            """SELECT * FROM practice_cards
               WHERE sentence_id=? AND card_type='sentence_order' AND active=1""",
            (sentence_id,),
        ).fetchone()
        session_id = connection.execute(
            """INSERT INTO practice_sessions(source,sentence_ids_json,total,created_at)
               VALUES(?,?,1,?)""",
            (source, json.dumps([sentence_id]), reviewed_at),
        ).lastrowid
        connection.execute(
            """INSERT INTO review_events(
                 card_id,sentence_id,session_id,rating,reviewed_at,duration_ms,is_new,
                 fsrs_state_before,fsrs_state_after,fsrs_step_before,fsrs_step_after,
                 stability_before,stability_after,difficulty_before,difficulty_after,
                 next_review_before,next_review_after,fsrs_version,created_at
               ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                card["id"],
                sentence_id,
                session_id,
                3,
                reviewed_at,
                1000,
                int(card["last_review_at"] is None),
                card["fsrs_state"],
                card["fsrs_state"],
                card["fsrs_step"],
                card["fsrs_step"],
                card["stability"],
                card["stability"],
                card["difficulty"],
                card["difficulty"],
                card["next_review_at"],
                card["next_review_at"],
                card["fsrs_version"],
                reviewed_at,
            ),
        )
    return session_id


def complete_good_session(client, sentences):
    practice = client.post(
        "/api/practice/sessions",
        json={"sentenceIds": [sentence["id"] for sentence in sentences]},
    )
    assert practice.status_code == 201
    session_id = practice.get_json()["sessionId"]
    for sentence in sentences:
        checked = client.post(
            f"/api/practice/sessions/{session_id}/attempts",
            json={
                "attemptId": str(uuid.uuid4()),
                "sentenceId": sentence["id"],
                "action": "check",
                "answerOrder": sentence["correctOrder"],
            },
        )
        assert checked.status_code == 200
    completed = client.post(
        f"/api/practice/sessions/{session_id}/complete", json={}
    )
    assert completed.status_code == 200
    return session_id


def complete_with_unanswered(client, sentences, answered):
    practice = client.post(
        "/api/practice/sessions",
        json={"sentenceIds": [sentence["id"] for sentence in sentences]},
    )
    assert practice.status_code == 201
    session_id = practice.get_json()["sessionId"]
    checked = client.post(
        f"/api/practice/sessions/{session_id}/attempts",
        json={
            "attemptId": str(uuid.uuid4()),
            "sentenceId": answered["id"],
            "action": "check",
            "answerOrder": answered["correctOrder"],
        },
    )
    assert checked.status_code == 200
    completed = client.post(
        f"/api/practice/sessions/{session_id}/complete",
        json={"confirmUnanswered": True},
    )
    assert completed.status_code == 200
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
        "dailyAutoReviewLimit": 50,
        "dailyKanjiReadingReviewLimit": 30,
    }
    with db.get_db() as connection:
        assert connection.execute(
            "SELECT value FROM settings WHERE key='daily_auto_review_limit'"
        ).fetchone()["value"] == "50"
        connection.execute(
            "DELETE FROM settings WHERE key='daily_auto_review_limit'"
        )
    assert client.get("/api/settings/daily-plan").get_json() == {
        "dailyAutoReviewLimit": 50,
        "dailyKanjiReadingReviewLimit": 30,
    }

    saved = client.put(
        "/api/settings/daily-plan", json={"dailyAutoReviewLimit": 73}
    )
    assert saved.status_code == 200
    assert saved.get_json()["dailyAutoReviewLimit"] == 73
    db.init_db(enable_fuzzing=False)
    assert client.get("/api/settings/daily-plan").get_json() == {
        "dailyAutoReviewLimit": 73,
        "dailyKanjiReadingReviewLimit": 30,
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
        "dailyAutoReviewLimit": 73,
        "dailyKanjiReadingReviewLimit": 30,
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


def test_report_retry_quota_caps_all_revalidates_and_completed_retry_consumes_quota(
    tmp_path, monkeypatch
):
    client, db, _ = load_app(tmp_path, monkeypatch)
    collection_id = default_collection_id(client)
    assert client.put(
        "/api/settings/daily-plan", json={"dailyAutoReviewLimit": 50}
    ).status_code == 200
    assert client.put(
        "/api/settings/timezone", json={"timezone": "Asia/Shanghai"}
    ).status_code == 200

    report_anchor = make_sentence(client, collection_id, "quota-report")
    report_id = complete_good_session(client, [report_anchor])
    completed_fillers = [
        make_sentence(client, collection_id, f"completed-{index}")
        for index in range(43)
    ]
    for sentence in completed_fillers:
        add_review_event(db, sentence["id"], "2026-01-01T15:00:00+00:00")
    candidates = [
        make_sentence(client, collection_id, f"candidate-{index}")
        for index in range(80)
    ]

    before_report_read = fsrs_snapshot(db)
    report = client.get(f"/api/reports/{report_id}").get_json()["report"]
    assert report["retry"] == {
        "candidateCount": 80,
        "availableCount": 6,
        "unansweredCount": 0,
        "remainingAutoReviewQuota": 6,
        "dailyAutoReviewLimit": 50,
        "quotaLimited": True,
    }
    assert fsrs_snapshot(db) == before_report_read

    retry_all = client.post(
        "/api/practice/sessions",
        json={"scope": "report_retry", "reportId": report_id, "count": "all"},
    )
    assert retry_all.status_code == 201
    assert len(retry_all.get_json()["sentences"]) == 6
    assert "剩余额度" in retry_all.get_json()["notice"]
    with db.get_db() as connection:
        assert connection.execute(
            "SELECT source,total FROM practice_sessions WHERE id=?",
            (retry_all.get_json()["sessionId"],),
        ).fetchone()["source"] == "report_retry"

    # The dialog can become stale. Recompute both the distinct completed count
    # and candidate queue under the session-creation transaction.
    add_review_event(db, candidates[0]["id"], "2026-01-01T15:10:00+00:00")
    stale_request = client.post(
        "/api/practice/sessions",
        json={
            "scope": "report_retry",
            "reportId": report_id,
            "count": "all",
            "expectedAvailableCount": 6,
        },
    )
    assert stale_request.status_code == 201
    stale_payload = stale_request.get_json()
    assert len(stale_payload["sentences"]) == 5
    assert candidates[0]["id"] not in {
        sentence["id"] for sentence in stale_payload["sentences"]
    }
    assert "已调整为 5 句" in stale_payload["notice"]

    # Completing a report_retry round still creates normal review_events, so
    # those distinct sentences consume the rest of the shared daily quota.
    retry_session_id = stale_payload["sessionId"]
    for sentence in stale_payload["sentences"]:
        checked = client.post(
            f"/api/practice/sessions/{retry_session_id}/attempts",
            json={
                "attemptId": str(uuid.uuid4()),
                "sentenceId": sentence["id"],
                "action": "check",
                "answerOrder": sentence["correctOrder"],
            },
        )
        assert checked.status_code == 200
    assert client.post(
        f"/api/practice/sessions/{retry_session_id}/complete", json={}
    ).status_code == 200
    dashboard = client.get("/api/dashboard").get_json()
    assert dashboard["completedToday"] == 50
    assert dashboard["remainingAutoReviewQuota"] == 0
    with db.get_db() as connection:
        assert connection.execute(
            "SELECT COUNT(*) n FROM review_events WHERE session_id=?",
            (retry_session_id,),
        ).fetchone()["n"] == 5


def test_report_retry_prioritizes_unanswered_then_reuses_home_queue_order(
    tmp_path, monkeypatch
):
    client, db, _ = load_app(tmp_path, monkeypatch)
    collection_id = default_collection_id(client)
    assert client.put(
        "/api/settings/daily-plan", json={"dailyAutoReviewLimit": 20}
    ).status_code == 200

    unanswered_future = make_sentence(client, collection_id, "retry-u-future")
    unanswered_due = make_sentence(client, collection_id, "retry-u-due")
    answered = make_sentence(client, collection_id, "retry-answered")
    report_id = complete_with_unanswered(
        client,
        [unanswered_future, unanswered_due, answered],
        answered,
    )
    high_risk = make_sentence(client, collection_id, "retry-high-risk")
    tied_late = make_sentence(client, collection_id, "retry-tied-late")
    tied_early = make_sentence(client, collection_id, "retry-tied-early")
    future_old = make_sentence(client, collection_id, "retry-future-old")
    newer_new = make_sentence(client, collection_id, "retry-newer-new")
    older_new = make_sentence(client, collection_id, "retry-older-new")

    make_old_card(
        db, unanswered_future["id"], stability=1.0,
        next_review_at="2099-01-01T00:00:00+00:00",
    )
    make_old_card(
        db, unanswered_due["id"], stability=5.0,
        next_review_at="2025-12-01T00:00:00+00:00",
    )
    make_old_card(
        db, high_risk["id"], stability=1.0,
        next_review_at="2025-12-31T00:00:00+00:00",
    )
    make_old_card(
        db, tied_late["id"], stability=10.0,
        next_review_at="2025-12-20T00:00:00+00:00",
    )
    make_old_card(
        db, tied_early["id"], stability=10.0,
        next_review_at="2025-12-10T00:00:00+00:00",
    )
    make_old_card(
        db, future_old["id"], stability=0.1,
        next_review_at="2099-01-01T00:00:00+00:00",
    )
    set_created_at(db, older_new["id"], "2025-01-01T00:00:00+00:00")
    set_created_at(db, newer_new["id"], "2025-02-01T00:00:00+00:00")

    home = client.post(
        "/api/practice/sessions",
        json={"collectionId": collection_id, "count": "all"},
    )
    assert home.status_code == 201
    home_ids = [sentence["id"] for sentence in home.get_json()["sentences"]]
    assert future_old["id"] not in home_ids
    supplemental_ids = [
        sentence_id
        for sentence_id in home_ids
        if sentence_id not in {unanswered_future["id"], unanswered_due["id"]}
    ]
    assert supplemental_ids == [
        high_risk["id"],
        tied_early["id"],
        tied_late["id"],
        older_new["id"],
        newer_new["id"],
    ]

    before_report_read = fsrs_snapshot(db)
    report = client.get(f"/api/reports/{report_id}").get_json()["report"]
    assert report["retry"] == {
        "candidateCount": 7,
        "availableCount": 7,
        "unansweredCount": 2,
        "remainingAutoReviewQuota": 19,
        "dailyAutoReviewLimit": 20,
        "quotaLimited": False,
    }
    assert fsrs_snapshot(db) == before_report_read
    retry = client.post(
        "/api/practice/sessions",
        json={"scope": "report_retry", "reportId": report_id, "count": "all"},
    )
    assert retry.status_code == 201
    retry_ids = [sentence["id"] for sentence in retry.get_json()["sentences"]]
    assert retry_ids == [
        unanswered_future["id"],
        unanswered_due["id"],
        *supplemental_ids,
    ]
    assert len(retry_ids) == len(set(retry_ids))

    # Unanswered items are still capped by quota and retain prior position.
    assert client.put(
        "/api/settings/daily-plan", json={"dailyAutoReviewLimit": 3}
    ).status_code == 200
    limited_report = client.get(f"/api/reports/{report_id}").get_json()["report"]
    assert limited_report["retry"]["candidateCount"] == 7
    assert limited_report["retry"]["availableCount"] == 2
    assert limited_report["retry"]["quotaLimited"] is True
    limited = client.post(
        "/api/practice/sessions",
        json={"scope": "report_retry", "reportId": report_id, "count": "all"},
    )
    assert [
        sentence["id"] for sentence in limited.get_json()["sentences"]
    ] == [unanswered_future["id"], unanswered_due["id"]]


def test_zero_quota_blocks_home_and_report_retry_but_not_manual_or_random(
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
    report = client.get(f"/api/reports/{report_id}").get_json()["report"]
    assert report["retry"]["candidateCount"] >= 1
    assert report["retry"]["availableCount"] == 0
    assert report["retry"]["remainingAutoReviewQuota"] == 0
    assert report["retry"]["quotaLimited"] is True
    retry_round = client.post(
        "/api/practice/sessions",
        json={"scope": "report_retry", "reportId": report_id, "count": "all"},
    )
    assert retry_round.status_code == 409
    assert retry_round.get_json()["remainingAutoReviewQuota"] == 0
    assert (
        retry_round.get_json()["error"]
        == "今日自动复习计划已完成，明天可继续自动复习。仍可进入句集进行专项练习。"
    )

    with db.get_db() as connection:
        sources = {
            row["source"]
            for row in connection.execute(
                "SELECT source FROM practice_sessions WHERE id>?",
                (sessions_before,),
            )
        }
    assert {"collection", "selected"} <= sources
    assert "report_retry" not in sources


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
    assert "今日自动复习计划已完成。仍可进入句集进行专项练习。" in source
    assert "首页自动复习和练习报告中的“再练一轮”共享每日额度" in source
    assert "专项练习和“再练一轮”不受此限制" not in source
    assert "或在练习报告中再练一轮" not in source
    assert "api('/api/settings/daily-plan')" in source
    assert 'id="daily-plan-form"' in source
    assert 'min="1" max="500" step="1"' in source
    assert "scope: 'collection'" in source
    assert "scope: 'report_retry'" in source
    assert "startPractice({sentenceIds:ids})" in source
    assert ".count-option,.custom-count input{min-height:48px}" in styles
    assert ".hero-bottom{display:grid;grid-template-columns:repeat(2,minmax(0,1fr))" in styles
    assert ".hero-bottom .auto-review-metric{grid-column:1/-1;grid-row:2}" in styles

    due_options = source.split("function dueCollectionOptions(selected) {", 1)[1].split(
        "function countPickerIds", 1
    )[0]
    assert "${esc(c.name)}</option>" in due_options
    assert "c.due" not in due_options
    assert "availableAutoReviewCount" not in due_options
    assert (
        "本句集有 ${due} 句待复习，本轮最多可自动安排 ${max} 句"
        in source
    )
    assert ".home-practice-picker>div,.home-practice-picker .field{min-width:0}" in styles
    assert (
        ".home-practice-picker select{width:100%;max-width:100%;min-width:0"
        in styles
    )
