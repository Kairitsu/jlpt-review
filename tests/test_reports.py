import importlib
import json
import uuid
from pathlib import Path


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


def make_sentence(client, collection_id, index):
    chunk_id = f"report-{index}"
    response = client.post("/api/sentences", json={
        "collectionId": collection_id,
        "chinese": f"报告句子 {index}",
        "japanese": f"文{index}",
        "chunks": [{"id": chunk_id, "text": f"文{index}"}],
        "correctOrder": [chunk_id],
    })
    assert response.status_code == 201
    return response.get_json()["sentence"]


def attempt(client, session_id, sentence, *, correct=False, skip=False):
    return client.post(f"/api/practice/sessions/{session_id}/attempts", json={
        "attemptId": str(uuid.uuid4()),
        "sentenceId": sentence["id"],
        "action": "skip" if skip else "check",
        "answerOrder": sentence["correctOrder"] if correct else [],
    })


def finish_question(client, session_id, sentence, *, easy=False):
    response = client.post(
        f"/api/practice/sessions/{session_id}/sentences/{sentence['id']}/complete",
        json={"easy": easy},
    )
    assert response.status_code == 200


def complete_good_session(client, sentence):
    practice = client.post(
        "/api/practice/sessions", json={"sentenceIds": [sentence["id"]]}
    ).get_json()
    session_id = practice["sessionId"]
    assert attempt(client, session_id, sentence, correct=True).status_code == 200
    finish_question(client, session_id, sentence)
    assert client.post(f"/api/practice/sessions/{session_id}/complete", json={}).status_code == 200
    return session_id


def test_report_and_history_use_persisted_fsrs_rating_counts(tmp_path, monkeypatch):
    client, _ = load_app(tmp_path, monkeypatch)
    collection = client.get("/api/dashboard").get_json()["collections"][0]
    sentences = [make_sentence(client, collection["id"], index) for index in range(5)]
    practice = client.post(
        "/api/practice/sessions", json={"sentenceIds": [item["id"] for item in sentences]}
    ).get_json()
    session_id = practice["sessionId"]

    # Again: ends wrong.
    assert attempt(client, session_id, sentences[0]).status_code == 200
    finish_question(client, session_id, sentences[0])
    # Hard: wrong, then correct.
    assert attempt(client, session_id, sentences[1]).status_code == 200
    assert attempt(client, session_id, sentences[1], correct=True).status_code == 200
    finish_question(client, session_id, sentences[1])
    # Good: correct on the first check.
    assert attempt(client, session_id, sentences[2], correct=True).status_code == 200
    finish_question(client, session_id, sentences[2])
    # Legacy Easy input is ignored: another first-check success is still Good
    # because it belongs to a different sentence with no reliable history.
    assert attempt(client, session_id, sentences[3], correct=True).status_code == 200
    finish_question(client, session_id, sentences[3], easy=True)
    # Skipped: no FSRS rating.
    assert attempt(client, session_id, sentences[4], skip=True).status_code == 200
    finish_question(client, session_id, sentences[4])
    assert client.post(f"/api/practice/sessions/{session_id}/complete", json={}).status_code == 200

    report = client.get(f"/api/reports/{session_id}").get_json()["report"]
    expected = {"again": 1, "hard": 1, "good": 2, "easy": 0, "skipped": 1}
    assert report["ratingCounts"] == expected
    assert [item["rating"] for item in report["items"]] == [
        "again", "hard", "good", "good", None,
    ]
    assert report["collection"] == {
        "id": collection["id"], "name": collection["name"], "available": 5,
        "dueCount": 1,
    }

    history = client.get("/api/reports").get_json()["reports"]
    history_report = next(item for item in history if item["id"] == session_id)
    assert history_report["ratingCounts"] == expected
    assert "accuracy" not in history_report


def test_report_keeps_original_collection_when_sentence_moves(tmp_path, monkeypatch):
    client, _ = load_app(tmp_path, monkeypatch)
    source = client.get("/api/dashboard").get_json()["collections"][0]
    target_id = client.post("/api/collections", json={"name": "移动目标"}).get_json()["id"]
    sentence = make_sentence(client, source["id"], "move")
    session_id = complete_good_session(client, sentence)

    moved = client.post("/api/sentences/move", json={
        "sentenceIds": [sentence["id"]], "targetCollectionId": target_id,
    })
    assert moved.status_code == 200

    report = client.get(f"/api/reports/{session_id}").get_json()["report"]
    assert report["collection"]["id"] == source["id"]
    assert report["collection"]["available"] == 0
    assert report["collection"]["dueCount"] == 0
    assert report["items"][0]["collectionId"] == source["id"]


def test_legacy_report_resolves_collection_from_live_sentence(tmp_path, monkeypatch):
    client, db = load_app(tmp_path, monkeypatch)
    source_id = client.get("/api/dashboard").get_json()["collections"][0]["id"]
    target_id = client.post("/api/collections", json={"name": "旧报告目标"}).get_json()["id"]
    sentence = make_sentence(client, source_id, "legacy")
    session_id = complete_good_session(client, sentence)

    with db.get_db() as connection:
        row = connection.execute(
            "SELECT id,sentence_snapshot_json FROM attempts WHERE session_id=?", (session_id,)
        ).fetchone()
        snapshot = json.loads(row["sentence_snapshot_json"])
        snapshot.pop("collectionId")
        connection.execute(
            "UPDATE attempts SET sentence_snapshot_json=? WHERE id=?",
            (json.dumps(snapshot, ensure_ascii=False), row["id"]),
        )
    assert client.post("/api/sentences/move", json={
        "sentenceIds": [sentence["id"]], "targetCollectionId": target_id,
    }).status_code == 200

    report = client.get(f"/api/reports/{session_id}").get_json()["report"]
    assert report["collection"]["id"] == target_id
    assert report["collection"]["available"] == 1
    assert report["collection"]["dueCount"] == 0


def test_report_retry_uses_current_due_scope_and_revalidates_count(tmp_path, monkeypatch):
    client, db = load_app(tmp_path, monkeypatch)
    source = client.get("/api/dashboard").get_json()["collections"][0]
    other_id = client.post("/api/collections", json={"name": "其他句集"}).get_json()["id"]
    report_sentence = make_sentence(client, source["id"], "completed")
    due_early = make_sentence(client, source["id"], "due-early")
    due_late = make_sentence(client, source["id"], "due-late")
    future = make_sentence(client, source["id"], "future")
    other_due = make_sentence(client, other_id, "other-due")
    session_id = complete_good_session(client, report_sentence)

    with db.get_db() as connection:
        connection.executemany(
            "UPDATE sentences SET next_review_at=? WHERE id=?",
            [
                ("2099-01-01T00:00:00+00:00", report_sentence["id"]),
                ("2000-01-01T00:00:01+00:00", due_early["id"]),
                ("2000-01-01T00:00:02+00:00", due_late["id"]),
                ("2099-01-01T00:00:00+00:00", future["id"]),
                ("1999-01-01T00:00:00+00:00", other_due["id"]),
            ],
        )

    report = client.get(f"/api/reports/{session_id}").get_json()["report"]
    assert report["collection"] == {
        "id": source["id"], "name": source["name"], "available": 4,
        "dueCount": 2,
    }
    assert report["retry"] == {"availableCount": 2, "unansweredCount": 0}

    # Simulate a stale open dialog: one candidate is no longer due before Start.
    with db.get_db() as connection:
        connection.execute(
            "UPDATE sentences SET next_review_at=? WHERE id=?",
            ("2099-01-01T00:00:00+00:00", due_early["id"]),
        )
    retry = client.post(
        "/api/practice/sessions",
        json={"scope": "report_retry", "reportId": session_id, "count": 2},
    )
    assert retry.status_code == 201
    payload = retry.get_json()
    assert [item["id"] for item in payload["sentences"]] == [due_late["id"]]
    assert "只有 1 句" in payload["notice"]
    with db.get_db() as connection:
        practice = connection.execute(
            "SELECT source FROM practice_sessions WHERE id=?", (payload["sessionId"],)
        ).fetchone()
    assert practice["source"] == "report_retry"

    # A stale page must not fall back to all collection sentences when none are due.
    with db.get_db() as connection:
        connection.execute(
            "UPDATE sentences SET next_review_at=? WHERE id=?",
            ("2099-01-01T00:00:00+00:00", due_late["id"]),
        )
    empty = client.post(
        "/api/practice/sessions",
        json={"scope": "report_retry", "reportId": session_id, "count": 2},
    )
    assert empty.status_code == 400
    assert empty.get_json()["error"] == "当前没有可再次练习的句子"


def test_force_complete_persists_unanswered_without_touching_fsrs(tmp_path, monkeypatch):
    client, db = load_app(tmp_path, monkeypatch)
    collection = client.get("/api/dashboard").get_json()["collections"][0]
    answered = make_sentence(client, collection["id"], "answered")
    wrong = make_sentence(client, collection["id"], "wrong")
    unanswered = make_sentence(client, collection["id"], "unanswered")
    practice = client.post(
        "/api/practice/sessions",
        json={"sentenceIds": [answered["id"], wrong["id"], unanswered["id"]]},
    ).get_json()
    session_id = practice["sessionId"]
    assert attempt(client, session_id, answered, correct=True).status_code == 200
    assert attempt(client, session_id, wrong).status_code == 200

    with db.get_db() as connection:
        before = dict(connection.execute(
            "SELECT * FROM sentences WHERE id=?", (unanswered["id"],)
        ).fetchone())

    payload = {
        "easySentenceIds": [answered["id"]],
        "draftAnswers": [
            {"sentenceId": answered["id"], "answerOrder": answered["correctOrder"]},
            {"sentenceId": wrong["id"], "answerOrder": []},
            {"sentenceId": unanswered["id"], "answerOrder": unanswered["correctOrder"]},
        ],
    }
    blocked = client.post(f"/api/practice/sessions/{session_id}/complete", json=payload)
    assert blocked.status_code == 409
    assert blocked.get_json()["unansweredCount"] == 1
    assert blocked.get_json()["requiresConfirmation"] is True
    with db.get_db() as connection:
        assert connection.execute("SELECT COUNT(*) n FROM review_events").fetchone()["n"] == 0
        assert connection.execute(
            "SELECT completed_at FROM practice_sessions WHERE id=?", (session_id,)
        ).fetchone()["completed_at"] is None

    completed = client.post(
        f"/api/practice/sessions/{session_id}/complete",
        json={**payload, "confirmUnanswered": True},
    )
    assert completed.status_code == 200
    assert completed.get_json()["unansweredCount"] == 1
    with db.get_db() as connection:
        after = dict(connection.execute(
            "SELECT * FROM sentences WHERE id=?", (unanswered["id"],)
        ).fetchone())
        for field in (
            "fsrs_state", "fsrs_step", "stability", "difficulty",
            "last_review_at", "next_review_at", "fsrs_version",
        ):
            assert after[field] == before[field]
        assert connection.execute(
            "SELECT COUNT(*) n FROM review_events WHERE sentence_id=?", (unanswered["id"],)
        ).fetchone()["n"] == 0
        assert connection.execute(
            "SELECT COUNT(*) n FROM review_events WHERE session_id=?", (session_id,)
        ).fetchone()["n"] == 2
        item = connection.execute(
            "SELECT * FROM practice_items WHERE session_id=? AND sentence_id=?",
            (session_id, unanswered["id"]),
        ).fetchone()
        assert item["unanswered_at"] is not None
        assert item["finalized_at"] is None and item["fsrs_rating"] is None

    report = client.get(f"/api/reports/{session_id}").get_json()["report"]
    assert report["unansweredCount"] == 1
    assert len(report["items"]) == 3
    unanswered_item = next(item for item in report["items"] if item["id"] == unanswered["id"])
    assert unanswered_item["status"] == "unanswered"
    assert unanswered_item["answerText"] == unanswered["japanese"]
    assert unanswered_item["rating"] is None
    assert report["ratingCounts"] == {
        "again": 1, "hard": 0, "good": 1, "easy": 0, "skipped": 0,
    }

    duplicate = client.post(
        f"/api/practice/sessions/{session_id}/complete",
        json={**payload, "confirmUnanswered": True},
    )
    assert duplicate.status_code == 200 and duplicate.get_json()["duplicate"] is True
    with db.get_db() as connection:
        assert connection.execute(
            "SELECT COUNT(*) n FROM review_events WHERE session_id=?", (session_id,)
        ).fetchone()["n"] == 2


def test_report_retry_prioritizes_unanswered_and_deduplicates_due(tmp_path, monkeypatch):
    client, db = load_app(tmp_path, monkeypatch)
    collection = client.get("/api/dashboard").get_json()["collections"][0]
    unanswered_future = make_sentence(client, collection["id"], "u-future")
    unanswered_due = make_sentence(client, collection["id"], "u-due")
    answered = make_sentence(client, collection["id"], "answered-retry")
    due_early = make_sentence(client, collection["id"], "due-early-retry")
    due_late = make_sentence(client, collection["id"], "due-late-retry")
    practice = client.post(
        "/api/practice/sessions",
        json={"sentenceIds": [unanswered_future["id"], unanswered_due["id"], answered["id"]]},
    ).get_json()
    session_id = practice["sessionId"]
    assert attempt(client, session_id, answered, correct=True).status_code == 200
    assert client.post(
        f"/api/practice/sessions/{session_id}/complete",
        json={"confirmUnanswered": True},
    ).status_code == 200

    with db.get_db() as connection:
        connection.executemany(
            "UPDATE sentences SET next_review_at=? WHERE id=?",
            [
                ("2099-01-01T00:00:00+00:00", unanswered_future["id"]),
                ("2000-01-01T00:00:03+00:00", unanswered_due["id"]),
                ("2099-01-01T00:00:00+00:00", answered["id"]),
                ("2000-01-01T00:00:01+00:00", due_early["id"]),
                ("2000-01-01T00:00:02+00:00", due_late["id"]),
            ],
        )

    report = client.get(f"/api/reports/{session_id}").get_json()["report"]
    assert report["retry"] == {"availableCount": 4, "unansweredCount": 2}
    retry_one = client.post("/api/practice/sessions", json={
        "scope": "report_retry", "reportId": session_id, "count": 1,
    }).get_json()
    assert [item["id"] for item in retry_one["sentences"]] == [unanswered_future["id"]]
    retry_three = client.post("/api/practice/sessions", json={
        "scope": "report_retry", "reportId": session_id, "count": 3,
    }).get_json()
    assert [item["id"] for item in retry_three["sentences"]] == [
        unanswered_future["id"], unanswered_due["id"], due_early["id"],
    ]


def test_unanswered_schema_migration_preserves_existing_reports(tmp_path, monkeypatch):
    client, db = load_app(tmp_path, monkeypatch)
    collection = client.get("/api/dashboard").get_json()["collections"][0]
    sentence = make_sentence(client, collection["id"], "migration")
    session_id = complete_good_session(client, sentence)
    with db.get_db() as connection:
        before = {
            table: connection.execute(f"SELECT COUNT(*) n FROM {table}").fetchone()["n"]
            for table in ("sentences", "practice_sessions", "practice_items", "attempts", "review_events")
        }
        connection.execute("DROP INDEX IF EXISTS idx_practice_items_unanswered")
        connection.execute("ALTER TABLE practice_items DROP COLUMN unanswered_at")
        connection.execute("ALTER TABLE practice_items DROP COLUMN draft_answer_order_json")
        connection.execute("ALTER TABLE practice_items DROP COLUMN sentence_snapshot_json")
        connection.execute("ALTER TABLE practice_sessions DROP COLUMN unanswered")
        connection.execute(
            "DELETE FROM schema_migrations WHERE version='practice_unanswered_v1'"
        )

    migrated_client, migrated_db = load_app(tmp_path, monkeypatch)
    with migrated_db.get_db() as connection:
        after = {
            table: connection.execute(f"SELECT COUNT(*) n FROM {table}").fetchone()["n"]
            for table in ("sentences", "practice_sessions", "practice_items", "attempts", "review_events")
        }
        assert after == before
        assert {row["name"] for row in connection.execute("PRAGMA table_info(practice_sessions)")} >= {"unanswered"}
        assert {row["name"] for row in connection.execute("PRAGMA table_info(practice_items)")} >= {
            "unanswered_at", "draft_answer_order_json", "sentence_snapshot_json",
        }
        assert connection.execute(
            "SELECT COUNT(*) n FROM schema_migrations WHERE version='practice_unanswered_v1'"
        ).fetchone()["n"] == 1
    report = migrated_client.get(f"/api/reports/{session_id}")
    assert report.status_code == 200
    assert report.get_json()["report"]["items"][0]["status"] == "correct"


def test_report_without_surviving_items_returns_empty_safe_metadata(tmp_path, monkeypatch):
    client, _ = load_app(tmp_path, monkeypatch)
    collection_id = client.get("/api/dashboard").get_json()["collections"][0]["id"]
    sentence = make_sentence(client, collection_id, "deleted")
    session_id = complete_good_session(client, sentence)
    assert client.delete(f"/api/sentences/{sentence['id']}").status_code == 200

    response = client.get(f"/api/reports/{session_id}")
    assert response.status_code == 200
    report = response.get_json()["report"]
    assert report["items"] == []
    assert report["collection"] is None
    assert report["ratingCounts"] == {
        "again": 0, "hard": 0, "good": 0, "easy": 0, "skipped": 0,
    }


def test_report_frontend_has_only_new_actions_and_route_scoped_fab():
    source = (Path(__file__).parents[1] / "static" / "app.js").read_text()
    assert "toggle-wrong" not in source
    assert "retry-report" not in source
    assert "retry-wrong" not in source
    assert "data-action=\"open-retry-round\"" in source
    assert "state.route === 'report'" in source
    assert "const max = Number(retry.availableCount || 0)" in source
    assert "state.report = (await api(`/api/reports/${reportId}`)).report;" in source
    assert "await startPractice({ scope: 'report_retry', reportId, count });" in source
    assert "当前没有可再次练习的句子" in source
    assert 'data-action="skip"' not in source
    assert "practice.items.flatMap" in source
    assert "data-action=\"previous-question\"" in source
    assert "'next-question'" in source
    assert "'submit-round'" in source
    assert "navigatePractice(delta)" in source
    navigate_flow = source.split("function navigatePractice(delta) {", 1)[1].split(
        "function roundSubmissionPayload", 1
    )[0]
    assert "/attempts" not in navigate_flow and "/complete" not in navigate_flow
    assert "将从该句集中重新随机抽取题目。" not in source


def test_practice_exit_uses_shared_idempotent_round_submission_and_report_route():
    source = (Path(__file__).parents[1] / "static" / "app.js").read_text()
    exit_flow = source.split("async function confirmExitPractice(button) {", 1)[1].split(
        "const secondaryRoutes", 1
    )[0]

    assert source.count('data-action="exit-practice"') == 2
    assert "openExitPracticeDialog()" in source
    assert "提前结束并提交？" in source
    assert "当前还没有完成任何题目" in source
    assert "已经完成" in source and "尚未完成" in source
    assert "未完成题目不会计入 FSRS，也不会被判定为错误或遗忘" in source
    assert "继续练习" in source
    assert "data-action=\"confirm-exit-practice\"" in source
    assert "data-action=\"abandon-practice\"" in source
    assert "else if (action === 'exit-practice') openExitPracticeDialog();" in source
    assert "completedPracticeCount(practice)" in source
    assert "...roundSubmissionPayload(practice, true)" in exit_flow
    assert "completionMode: 'early_exit'" in exit_flow
    assert "/api/practice/sessions/${practice.sessionId}/complete" in exit_flow
    assert "/sentences/${" not in exit_flow
    assert "route('report', { reportId: submission.reportId })" in exit_flow
    assert "finalizeCurrentQuestion" not in source
    assert "buttons.forEach(item => { item.disabled = true; });" in exit_flow
    assert "buttons.forEach(item => { item.disabled = false; });" in exit_flow
    assert "if (errorEl) errorEl.textContent = error.message;" in exit_flow
    assert "if (!practice || practice.exiting) return;" in exit_flow
    assert "isExitPracticeDialogOpen() || isUnansweredPracticeDialogOpen()" in source
    assert "event.target.id === 'dialog'" in source
