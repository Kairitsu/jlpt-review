import importlib
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest


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
    flask_app = app.create_app({"TESTING": True})
    return flask_app, db


def make_sentence(client, collection_id, suffix):
    chunk_id = f"auto-{suffix}"
    response = client.post("/api/sentences", json={
        "collectionId": collection_id,
        "chinese": f"自动评分 {suffix}",
        "japanese": f"文{suffix}",
        "chunks": [{"id": chunk_id, "text": f"文{suffix}"}],
        "correctOrder": [chunk_id],
    })
    assert response.status_code == 201
    return response.get_json()["sentence"]


def start_session(client, sentence):
    response = client.post(
        "/api/practice/sessions", json={"sentenceIds": [sentence["id"]]}
    )
    assert response.status_code == 201
    return response.get_json()["sessionId"]


def submit_check(
    client,
    session_id,
    sentence,
    correct,
    *,
    attempt_id=None,
    duration_ms=0,
):
    answer_order = sentence["correctOrder"] if correct else []
    return submit_answer_order(
        client,
        session_id,
        sentence,
        answer_order,
        attempt_id=attempt_id,
        duration_ms=duration_ms,
    )


def submit_answer_order(
    client,
    session_id,
    sentence,
    answer_order,
    *,
    attempt_id=None,
    duration_ms=0,
):
    attempt_id = attempt_id or str(uuid.uuid4())
    response = client.post(
        f"/api/practice/sessions/{session_id}/attempts",
        json={
            "attemptId": attempt_id,
            "sentenceId": sentence["id"],
            "action": "check",
            "answerOrder": answer_order,
            "durationMs": duration_ms,
        },
    )
    assert response.status_code == 200
    return response.get_json()


def complete_round(
    client,
    session_id,
    sentence,
    checks,
    *,
    mode="normal",
    durations=None,
    complete_payload=None,
):
    durations = durations or [0] * len(checks)
    for correct, duration_ms in zip(checks, durations, strict=True):
        submit_check(
            client,
            session_id,
            sentence,
            correct,
            duration_ms=duration_ms,
        )
    payload = dict(complete_payload or {})
    if mode == "early_exit":
        payload.update({
            "completionMode": "early_exit",
            "confirmUnanswered": True,
            "draftAnswers": [{
                "sentenceId": sentence["id"],
                "answerOrder": sentence["correctOrder"] if checks and checks[-1] else [],
            }],
        })
    response = client.post(
        f"/api/practice/sessions/{session_id}/complete", json=payload
    )
    assert response.status_code == 200
    report = client.get(f"/api/reports/{session_id}").get_json()["report"]
    return report["items"][0]["rating"]


def event_for(db, session_id):
    with db.get_db() as connection:
        row = connection.execute(
            "SELECT * FROM review_events WHERE session_id=?", (session_id,)
        ).fetchone()
        return dict(row) if row else None


def test_consecutive_first_check_chain_upgrades_breaks_and_restarts(tmp_path, monkeypatch):
    flask_app, db = load_app(tmp_path, monkeypatch)
    client = flask_app.test_client()
    collection_id = client.get("/api/dashboard").get_json()["collections"][0]["id"]
    sentence = make_sentence(client, collection_id, "chain")

    sessions = []
    ratings = []
    for checks in ([True], [True], [True], [False, True], [True], [True]):
        session_id = start_session(client, sentence)
        sessions.append(session_id)
        ratings.append(complete_round(client, session_id, sentence, checks))

    assert ratings == ["good", "easy", "easy", "hard", "good", "easy"]
    events = [event_for(db, session_id) for session_id in sessions]
    assert [event["first_attempt_correct"] for event in events] == [1, 1, 1, 0, 1, 1]
    assert [event["rating_policy_version"] for event in events] == [2] * 6
    assert events[3]["attempt_count"] == 2
    assert events[3]["second_attempt_correct"] == 1


@pytest.mark.parametrize(
    "checks,expected,second_correct",
    [
        ([False], "again", None),
        ([False, True], "hard", 1),
        ([False, False], "again", 0),
        ([False, False, True], "again", 0),
        ([False, False, False, True], "again", 0),
    ],
)
def test_wrong_check_boundary_locks_again_after_second_wrong(
    checks, expected, second_correct, tmp_path, monkeypatch
):
    flask_app, db = load_app(tmp_path, monkeypatch)
    client = flask_app.test_client()
    collection_id = client.get("/api/dashboard").get_json()["collections"][0]["id"]
    sentence = make_sentence(client, collection_id, f"boundary-{len(checks)}-{expected}")
    session_id = start_session(client, sentence)

    assert complete_round(client, session_id, sentence, checks) == expected
    event = event_for(db, session_id)
    assert event["attempt_count"] == len(checks)
    assert event["first_attempt_correct"] == 0
    assert event["second_attempt_correct"] == second_correct
    assert event["final_attempt_correct"] == int(checks[-1])


def test_one_wrong_check_is_again_for_normal_and_early_exit(tmp_path, monkeypatch):
    flask_app, db = load_app(tmp_path, monkeypatch)
    client = flask_app.test_client()
    collection_id = client.get("/api/dashboard").get_json()["collections"][0]["id"]
    normal_sentence = make_sentence(client, collection_id, "normal-one-wrong")
    early_sentence = make_sentence(client, collection_id, "early-one-wrong")

    normal_session = start_session(client, normal_sentence)
    early_session = start_session(client, early_sentence)
    assert complete_round(client, normal_session, normal_sentence, [False]) == "again"
    assert complete_round(
        client, early_session, early_sentence, [False], mode="early_exit"
    ) == "again"
    assert event_for(db, normal_session)["second_attempt_correct"] is None
    assert event_for(db, early_session)["second_attempt_correct"] is None


def test_unanswered_does_not_write_fsrs_or_break_first_check_continuity(tmp_path, monkeypatch):
    flask_app, db = load_app(tmp_path, monkeypatch)
    client = flask_app.test_client()
    collection_id = client.get("/api/dashboard").get_json()["collections"][0]["id"]
    sentence = make_sentence(client, collection_id, "unanswered-continuity")

    first_session = start_session(client, sentence)
    assert complete_round(client, first_session, sentence, [True]) == "good"
    with db.get_db() as connection:
        before = dict(connection.execute(
            "SELECT * FROM sentences WHERE id=?", (sentence["id"],)
        ).fetchone())

    unanswered_session = start_session(client, sentence)
    completed = client.post(
        f"/api/practice/sessions/{unanswered_session}/complete",
        json={
            "confirmUnanswered": True,
            "draftAnswers": [{
                "sentenceId": sentence["id"],
                "answerOrder": sentence["correctOrder"],
            }],
        },
    )
    assert completed.status_code == 200
    report = client.get(
        f"/api/reports/{unanswered_session}"
    ).get_json()["report"]
    assert report["items"][0]["status"] == "unanswered"
    assert report["items"][0]["rating"] is None
    assert report["ratingCounts"] == {
        "again": 0, "hard": 0, "good": 0, "easy": 0, "skipped": 0,
    }
    assert event_for(db, unanswered_session) is None
    with db.get_db() as connection:
        after = dict(connection.execute(
            "SELECT * FROM sentences WHERE id=?", (sentence["id"],)
        ).fetchone())
    assert all(after[field] == before[field] for field in FSRS_FIELDS)

    following_session = start_session(client, sentence)
    assert complete_round(client, following_session, sentence, [True]) == "easy"


def test_duplicate_attempt_request_returns_original_and_cannot_invent_second_wrong(
    tmp_path, monkeypatch
):
    flask_app, db = load_app(tmp_path, monkeypatch)
    client = flask_app.test_client()
    collection_id = client.get("/api/dashboard").get_json()["collections"][0]["id"]
    sentence = make_sentence(client, collection_id, "attempt-idempotency")
    session_id = start_session(client, sentence)
    attempt_id = str(uuid.uuid4())

    first = submit_check(client, session_id, sentence, False, attempt_id=attempt_id)
    duplicate = submit_check(client, session_id, sentence, False, attempt_id=attempt_id)
    assert first["attemptNumber"] == duplicate["attemptNumber"] == 1
    assert duplicate["duplicate"] is True
    with db.get_db() as connection:
        attempts = connection.execute(
            """SELECT client_attempt_id,attempt_number FROM attempts
               WHERE session_id=? AND sentence_id=?""",
            (session_id, sentence["id"]),
        ).fetchall()
        assert [(row["client_attempt_id"], row["attempt_number"]) for row in attempts] == [
            (attempt_id, 1)
        ]

    assert complete_round(client, session_id, sentence, []) == "again"
    event = event_for(db, session_id)
    assert event["attempt_count"] == 1
    assert event["second_attempt_correct"] is None

    after_completion = submit_check(
        client, session_id, sentence, False, attempt_id=attempt_id
    )
    assert after_completion["duplicate"] is True


def test_blank_and_partial_answer_orders_use_normal_wrong_rating_rules(
    tmp_path, monkeypatch
):
    flask_app, db = load_app(tmp_path, monkeypatch)
    client = flask_app.test_client()
    collection_id = client.get("/api/dashboard").get_json()["collections"][0]["id"]
    response = client.post("/api/sentences", json={
        "collectionId": collection_id,
        "chinese": "未完成核对",
        "japanese": "文を作る",
        "chunks": [
            {"id": "incomplete-first", "text": "文を"},
            {"id": "incomplete-second", "text": "作る"},
        ],
        "correctOrder": ["incomplete-first", "incomplete-second"],
    })
    assert response.status_code == 201
    sentence = response.get_json()["sentence"]

    blank_session = start_session(client, sentence)
    blank_attempt_id = str(uuid.uuid4())
    blank = submit_answer_order(
        client, blank_session, sentence, [], attempt_id=blank_attempt_id
    )
    duplicate = submit_answer_order(
        client, blank_session, sentence, [], attempt_id=blank_attempt_id
    )
    assert blank["status"] == duplicate["status"] == "wrong"
    assert duplicate["duplicate"] is True
    assert complete_round(client, blank_session, sentence, []) == "again"

    partial_session = start_session(client, sentence)
    partial = submit_answer_order(
        client, partial_session, sentence, [sentence["correctOrder"][0]]
    )
    corrected = submit_answer_order(
        client, partial_session, sentence, sentence["correctOrder"]
    )
    assert partial["status"] == "wrong"
    assert corrected["status"] == "correct"
    assert complete_round(client, partial_session, sentence, []) == "hard"

    with db.get_db() as connection:
        blank_attempts = connection.execute(
            "SELECT status FROM attempts WHERE session_id=? ORDER BY attempt_number",
            (blank_session,),
        ).fetchall()
        partial_attempts = connection.execute(
            "SELECT status FROM attempts WHERE session_id=? ORDER BY attempt_number",
            (partial_session,),
        ).fetchall()
    assert [row["status"] for row in blank_attempts] == ["wrong"]
    assert [row["status"] for row in partial_attempts] == ["wrong", "correct"]


def test_attempt_numbers_are_backend_ordered_and_client_id_is_globally_unique(
    tmp_path, monkeypatch
):
    flask_app, db = load_app(tmp_path, monkeypatch)
    client = flask_app.test_client()
    collection_id = client.get("/api/dashboard").get_json()["collections"][0]["id"]
    sentence = make_sentence(client, collection_id, "attempt-order")
    first_session = start_session(client, sentence)
    first_id = str(uuid.uuid4())
    first = submit_check(client, first_session, sentence, False, attempt_id=first_id)
    second = submit_check(client, first_session, sentence, True)
    assert (first["attemptNumber"], second["attemptNumber"]) == (1, 2)

    second_session = start_session(client, sentence)
    conflict = client.post(
        f"/api/practice/sessions/{second_session}/attempts",
        json={
            "attemptId": first_id,
            "sentenceId": sentence["id"],
            "action": "check",
            "answerOrder": sentence["correctOrder"],
        },
    )
    assert conflict.status_code == 409
    with db.get_db() as connection:
        assert connection.execute(
            "SELECT COUNT(*) n FROM attempts WHERE client_attempt_id=?", (first_id,)
        ).fetchone()["n"] == 1


def test_single_item_and_round_settlement_are_idempotent_and_ignore_legacy_easy(
    tmp_path, monkeypatch
):
    flask_app, db = load_app(tmp_path, monkeypatch)
    client = flask_app.test_client()
    collection_id = client.get("/api/dashboard").get_json()["collections"][0]["id"]
    sentence = make_sentence(client, collection_id, "settlement-idempotency")
    session_id = start_session(client, sentence)
    submit_check(client, session_id, sentence, True)

    first = client.post(
        f"/api/practice/sessions/{session_id}/sentences/{sentence['id']}/complete",
        json={"easy": True},
    )
    duplicate_item = client.post(
        f"/api/practice/sessions/{session_id}/sentences/{sentence['id']}/complete",
        json={"easy": True},
    )
    assert first.status_code == duplicate_item.status_code == 200
    assert first.get_json()["rating"] == "good"
    assert duplicate_item.get_json()["duplicate"] is True

    first_round = client.post(
        f"/api/practice/sessions/{session_id}/complete",
        json={"easySentenceIds": [sentence["id"]]},
    )
    duplicate_round = client.post(
        f"/api/practice/sessions/{session_id}/complete",
        json={"easySentenceIds": "malformed legacy value"},
    )
    assert first_round.status_code == duplicate_round.status_code == 200
    assert duplicate_round.get_json()["duplicate"] is True
    with db.get_db() as connection:
        assert connection.execute(
            "SELECT COUNT(*) n FROM review_events WHERE session_id=?", (session_id,)
        ).fetchone()["n"] == 1
        assert connection.execute(
            "SELECT easy_selected FROM practice_items WHERE session_id=?", (session_id,)
        ).fetchone()["easy_selected"] == 0
    assert len([
        row for row in client.get("/api/reports").get_json()["reports"]
        if row["id"] == session_id
    ]) == 1


@pytest.mark.parametrize(
    "checks,expected",
    [([True], "good"), ([False, True], "hard"), ([False], "again")],
)
def test_normal_and_early_exit_share_the_same_rating_entrypoint(
    checks, expected, tmp_path, monkeypatch
):
    flask_app, _ = load_app(tmp_path, monkeypatch)
    client = flask_app.test_client()
    collection_id = client.get("/api/dashboard").get_json()["collections"][0]["id"]
    normal_sentence = make_sentence(client, collection_id, f"normal-{expected}")
    early_sentence = make_sentence(client, collection_id, f"early-{expected}")
    normal_session = start_session(client, normal_sentence)
    early_session = start_session(client, early_sentence)

    assert complete_round(client, normal_session, normal_sentence, checks) == expected
    assert complete_round(
        client, early_session, early_sentence, checks, mode="early_exit"
    ) == expected


def test_duration_never_changes_rating(tmp_path, monkeypatch):
    flask_app, db = load_app(tmp_path, monkeypatch)
    client = flask_app.test_client()
    collection_id = client.get("/api/dashboard").get_json()["collections"][0]["id"]
    fast = make_sentence(client, collection_id, "fast")
    slow = make_sentence(client, collection_id, "slow")
    fast_session = start_session(client, fast)
    slow_session = start_session(client, slow)

    assert complete_round(
        client, fast_session, fast, [False, True], durations=[1, 2]
    ) == "hard"
    assert complete_round(
        client, slow_session, slow, [False, True], durations=[60_000, 900_000]
    ) == "hard"
    assert event_for(db, fast_session)["duration_ms"] == 3
    assert event_for(db, slow_session)["duration_ms"] == 960_000


def test_second_attempt_null_and_false_are_distinct_in_review_events(tmp_path, monkeypatch):
    flask_app, db = load_app(tmp_path, monkeypatch)
    client = flask_app.test_client()
    collection_id = client.get("/api/dashboard").get_json()["collections"][0]["id"]
    one_wrong = make_sentence(client, collection_id, "one-wrong")
    two_wrong = make_sentence(client, collection_id, "two-wrong")
    one_session = start_session(client, one_wrong)
    two_session = start_session(client, two_wrong)

    complete_round(client, one_session, one_wrong, [False])
    complete_round(client, two_session, two_wrong, [False, False])
    assert event_for(db, one_session)["second_attempt_correct"] is None
    assert event_for(db, two_session)["second_attempt_correct"] == 0


def test_unreliable_old_history_is_not_guessed_from_rating(tmp_path, monkeypatch):
    flask_app, db = load_app(tmp_path, monkeypatch)
    client = flask_app.test_client()
    collection_id = client.get("/api/dashboard").get_json()["collections"][0]["id"]
    sentence = make_sentence(client, collection_id, "unknown-history")
    old_session = start_session(client, sentence)
    assert complete_round(client, old_session, sentence, [True]) == "good"
    with db.get_db() as connection:
        connection.execute(
            """UPDATE review_events
               SET first_attempt_correct=NULL,second_attempt_correct=NULL,
                   final_attempt_correct=NULL,attempt_count=0,rating_policy_version=1
               WHERE session_id=?""",
            (old_session,),
        )

    new_session = start_session(client, sentence)
    assert complete_round(client, new_session, sentence, [True]) == "good"


def test_v2_migration_preserves_cards_reports_and_old_ratings(tmp_path, monkeypatch):
    flask_app, db = load_app(tmp_path, monkeypatch)
    client = flask_app.test_client()
    collection_id = client.get("/api/dashboard").get_json()["collections"][0]["id"]
    sentence = make_sentence(client, collection_id, "migration")
    first_session = start_session(client, sentence)
    second_session = start_session(client, sentence)
    assert complete_round(client, first_session, sentence, [True]) == "good"
    assert complete_round(client, second_session, sentence, [True]) == "easy"
    reports_before = {
        session_id: client.get(f"/api/reports/{session_id}").get_json()["report"]
        for session_id in (first_session, second_session)
    }
    with db.get_db() as connection:
        counts_before = {
            table: connection.execute(f"SELECT COUNT(*) n FROM {table}").fetchone()["n"]
            for table in (
                "sentences", "practice_sessions", "practice_items", "attempts", "review_events"
            )
        }
        card_before = dict(connection.execute(
            "SELECT * FROM sentences WHERE id=?", (sentence["id"],)
        ).fetchone())
        connection.execute("DROP INDEX ux_attempts_client_id")
        connection.execute("DROP INDEX ux_attempts_number")
        connection.execute("ALTER TABLE attempts DROP COLUMN client_attempt_id")
        connection.execute("ALTER TABLE attempts DROP COLUMN attempt_number")
        for column in (
            "attempt_count",
            "first_attempt_correct",
            "second_attempt_correct",
            "final_attempt_correct",
            "rating_policy_version",
        ):
            connection.execute(f"ALTER TABLE review_events DROP COLUMN {column}")
        connection.execute(
            "DELETE FROM schema_migrations WHERE version='fsrs_automatic_rating_v2'"
        )

    migrated_app, migrated_db = load_app(tmp_path, monkeypatch)
    migrated_client = migrated_app.test_client()
    with migrated_db.get_db() as connection:
        counts_after = {
            table: connection.execute(f"SELECT COUNT(*) n FROM {table}").fetchone()["n"]
            for table in counts_before
        }
        card_after = dict(connection.execute(
            "SELECT * FROM sentences WHERE id=?", (sentence["id"],)
        ).fetchone())
        events = connection.execute(
            """SELECT rating,attempt_count,first_attempt_correct,
                      second_attempt_correct,rating_policy_version
               FROM review_events ORDER BY session_id"""
        ).fetchall()
        assert counts_after == counts_before
        assert all(card_after[field] == card_before[field] for field in FSRS_FIELDS)
        assert [row["rating"] for row in events] == [3, 4]
        assert [row["rating_policy_version"] for row in events] == [1, 1]
        assert [row["first_attempt_correct"] for row in events] == [1, 1]
        assert connection.execute(
            """SELECT COUNT(*) n FROM schema_migrations
               WHERE version='fsrs_automatic_rating_v2'"""
        ).fetchone()["n"] == 1

    for session_id, before in reports_before.items():
        after = migrated_client.get(
            f"/api/reports/{session_id}"
        ).get_json()["report"]
        assert after["items"][0]["rating"] == before["items"][0]["rating"]
        assert after["ratingCounts"] == before["ratingCounts"]

    # Reliably backfilled old first-check facts may continue the chain.
    following_session = start_session(migrated_client, sentence)
    assert complete_round(
        migrated_client, following_session, sentence, [True]
    ) == "easy"


def test_concurrent_item_settlement_writes_one_review_and_one_state_update(
    tmp_path, monkeypatch
):
    flask_app, db = load_app(tmp_path, monkeypatch)
    client = flask_app.test_client()
    collection_id = client.get("/api/dashboard").get_json()["collections"][0]["id"]
    sentence = make_sentence(client, collection_id, "concurrent")
    session_id = start_session(client, sentence)
    submit_check(client, session_id, sentence, True)

    def finalize():
        with flask_app.test_client() as thread_client:
            response = thread_client.post(
                f"/api/practice/sessions/{session_id}/sentences/{sentence['id']}/complete",
                json={},
            )
            return response.status_code, response.get_json()

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: finalize(), range(2)))
    assert [status for status, _ in results] == [200, 200]
    assert sum(bool(payload.get("duplicate")) for _, payload in results) == 1
    with db.get_db() as connection:
        assert connection.execute(
            """SELECT COUNT(*) n FROM review_events
               WHERE session_id=? AND sentence_id=?""",
            (session_id, sentence["id"]),
        ).fetchone()["n"] == 1
        assert connection.execute(
            """SELECT COUNT(*) n FROM practice_items
               WHERE session_id=? AND sentence_id=? AND finalized_at IS NOT NULL""",
            (session_id, sentence["id"]),
        ).fetchone()["n"] == 1


def test_frontend_has_no_manual_easy_and_preserves_attempt_id_across_retry():
    source = (Path(__file__).parents[1] / "static" / "app.js").read_text()
    for removed in ("太简单", "easySelected", "easySentenceIds", "easy-rating"):
        assert removed not in source
    assert "createClientAttemptId()" in source
    assert "pendingAttempt" in source
    assert "attemptId:item.pendingAttempt.id" in source
    assert "item.pendingAttempt = null;" in source
    assert "答题耗时" not in source
    assert "连续第二轮起仍首次答对为“轻松掌握”" in source
    assert "从未核对的题目不计入 FSRS" in source

    navigation = source.split("function navigatePractice(delta) {", 1)[1].split(
        "function roundSubmissionPayload", 1
    )[0]
    assert "/attempts" not in navigation and "/complete" not in navigation


def test_incomplete_answer_check_uses_modal_and_preserves_normal_attempt_flow():
    source = (Path(__file__).parents[1] / "static" / "app.js").read_text()
    complete_predicate = source.split(
        "function practiceAnswerComplete", 1
    )[1].split("function practiceReadyToCheck", 1)[0]
    ready_predicate = source.split(
        "function practiceReadyToCheck", 1
    )[1].split("function moveSelectedTo", 1)[0]
    record_flow = source.split(
        "async function record(action", 1
    )[1].split("function navigatePractice", 1)[0]

    assert "item.selected.length === item.candidates.length" in complete_predicate
    assert "selected.length" not in ready_predicate
    assert "!item.checked" in ready_predicate
    assert "!item.submitting" in ready_predicate
    assert "!practice.submittingRound" in ready_predicate
    assert "!practice.exiting" in ready_predicate
    assert "当前句子尚未排列完成。直接查看答案会将本次回答记录为错误，并参与本题的自动评分。" in source
    assert 'data-action="continue-incomplete-answer"' in source
    assert 'data-action="confirm-incomplete-answer"' in source
    assert "if (!practiceAnswerComplete(item) && !confirmIncomplete)" in record_flow
    assert "const answerOrder = [...item.selected];" in record_flow
    assert "attemptId:item.pendingAttempt.id" in record_flow
    assert "dialogButtons.forEach(item => { item.disabled = true; });" in record_flow
    assert "dialogButtons.forEach(item => { item.disabled = false; });" in record_flow
    assert "if (errorEl) errorEl.textContent = error.message;" in record_flow
    assert "if (button) closeDialog();" in record_flow
    assert "await record('check', {confirmIncomplete:true, button});" in source
    assert "请先把所有词块摆放完整" not in source
    assert "practiceDialogBusy()" in source
