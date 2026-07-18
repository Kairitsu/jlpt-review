import importlib
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
    return app.create_app({"TESTING": True}).test_client(), db


def make_sentence(client, collection_id, index):
    chunk_id = f"early-exit-{index}"
    response = client.post("/api/sentences", json={
        "collectionId": collection_id,
        "chinese": f"提前结束句子 {index}",
        "japanese": f"文{index}",
        "chunks": [{"id": chunk_id, "text": f"文{index}"}],
        "correctOrder": [chunk_id],
    })
    assert response.status_code == 201
    return response.get_json()["sentence"]


def start_session(client, sentences):
    response = client.post(
        "/api/practice/sessions",
        json={"sentenceIds": [sentence["id"] for sentence in sentences]},
    )
    assert response.status_code == 201
    return response.get_json()["sessionId"]


def check_answer(client, session_id, sentence, *, correct):
    response = client.post(
        f"/api/practice/sessions/{session_id}/attempts",
        json={
            "attemptId": str(uuid.uuid4()),
            "sentenceId": sentence["id"],
            "action": "check",
            "answerOrder": sentence["correctOrder"] if correct else [],
        },
    )
    assert response.status_code == 200
    return response.get_json()


def early_exit(client, session_id, sentences, **extra):
    payload = {
        "completionMode": "early_exit",
        "confirmUnanswered": True,
        "easySentenceIds": [],
        "draftAnswers": [
            {"sentenceId": sentence["id"], "answerOrder": []}
            for sentence in sentences
        ],
        **extra,
    }
    return client.post(f"/api/practice/sessions/{session_id}/complete", json=payload)


def test_early_exit_generates_report_and_only_schedules_checked_items(tmp_path, monkeypatch):
    client, db = load_app(tmp_path, monkeypatch)
    collection_id = client.get("/api/dashboard").get_json()["collections"][0]["id"]
    good, wrong, arranged_only = [
        make_sentence(client, collection_id, index) for index in range(3)
    ]
    session_id = start_session(client, [good, wrong, arranged_only])
    assert check_answer(client, session_id, good, correct=True)["status"] == "correct"
    assert check_answer(client, session_id, wrong, correct=False)["status"] == "wrong"

    with db.get_db() as connection:
        untouched_before = dict(connection.execute(
            "SELECT * FROM sentences WHERE id=?", (arranged_only["id"],)
        ).fetchone())

    response = early_exit(
        client,
        session_id,
        [good, wrong, arranged_only],
        easySentenceIds=[good["id"]],
        draftAnswers=[
            {"sentenceId": good["id"], "answerOrder": good["correctOrder"]},
            {"sentenceId": wrong["id"], "answerOrder": []},
            {
                "sentenceId": arranged_only["id"],
                "answerOrder": arranged_only["correctOrder"],
            },
        ],
    )
    assert response.status_code == 200
    assert response.get_json() == {
        "ok": True,
        "reportId": session_id,
        "completionMode": "early_exit",
        "endedEarly": True,
        "plannedCount": 3,
        "completedCount": 2,
        "unansweredCount": 1,
    }

    with db.get_db() as connection:
        events = connection.execute(
            "SELECT sentence_id,rating FROM review_events WHERE session_id=? ORDER BY sentence_id",
            (session_id,),
        ).fetchall()
        assert {(row["sentence_id"], row["rating"]) for row in events} == {
            (good["id"], 3),
            (wrong["id"], 1),
        }
        untouched_after = dict(connection.execute(
            "SELECT * FROM sentences WHERE id=?", (arranged_only["id"],)
        ).fetchone())
        for field in FSRS_FIELDS:
            assert untouched_after[field] == untouched_before[field]
        untouched_item = connection.execute(
            "SELECT * FROM practice_items WHERE session_id=? AND sentence_id=?",
            (session_id, arranged_only["id"]),
        ).fetchone()
        assert untouched_item["unanswered_at"] is not None
        assert untouched_item["finalized_at"] is None
        assert untouched_item["fsrs_rating"] is None

    report = client.get(f"/api/reports/{session_id}").get_json()["report"]
    assert report["completionMode"] == "early_exit"
    assert report["endedEarly"] is True
    assert report["plannedCount"] == 3
    assert report["completedCount"] == 2
    assert report["unansweredCount"] == 1
    assert report["correct"] == 1 and report["wrong"] == 1
    assert report["ratingCounts"] == {
        "again": 1, "hard": 0, "good": 1, "easy": 0, "skipped": 0,
    }
    by_id = {item["id"]: item for item in report["items"]}
    assert by_id[good["id"]]["rating"] == "good"
    assert by_id[wrong["id"]]["rating"] == "again"
    assert by_id[arranged_only["id"]]["status"] == "unanswered"
    assert by_id[arranged_only["id"]]["answerText"] == arranged_only["japanese"]
    assert by_id[arranged_only["id"]]["rating"] is None

    history = client.get("/api/reports").get_json()["reports"]
    history_row = next(row for row in history if row["id"] == session_id)
    assert history_row["endedEarly"] is True
    assert history_row["completedCount"] == 2
    assert history_row["unansweredCount"] == 1


def test_early_exit_all_answered_and_retries_are_idempotent(tmp_path, monkeypatch):
    client, db = load_app(tmp_path, monkeypatch)
    collection_id = client.get("/api/dashboard").get_json()["collections"][0]["id"]
    first, current = [make_sentence(client, collection_id, index) for index in ("first", "current")]
    session_id = start_session(client, [first, current])
    check_answer(client, session_id, first, correct=True)
    finalized = client.post(
        f"/api/practice/sessions/{session_id}/sentences/{first['id']}/complete",
        json={"easy": False},
    )
    assert finalized.status_code == 200
    check_answer(client, session_id, current, correct=True)

    first_exit = early_exit(client, session_id, [first, current])
    assert first_exit.status_code == 200
    assert first_exit.get_json()["completedCount"] == 2
    assert first_exit.get_json()["unansweredCount"] == 0
    assert first_exit.get_json()["endedEarly"] is True

    duplicate = early_exit(
        client,
        session_id,
        [first, current],
        easySentenceIds=[first["id"], current["id"]],
    )
    assert duplicate.status_code == 200
    assert duplicate.get_json()["duplicate"] is True
    assert duplicate.get_json()["completionMode"] == "early_exit"

    with db.get_db() as connection:
        assert connection.execute(
            "SELECT COUNT(*) n FROM review_events WHERE session_id=?", (session_id,)
        ).fetchone()["n"] == 2
        assert connection.execute(
            "SELECT COUNT(*) n FROM practice_sessions WHERE id=? AND completed_at IS NOT NULL",
            (session_id,),
        ).fetchone()["n"] == 1
    reports = [
        report for report in client.get("/api/reports").get_json()["reports"]
        if report["id"] == session_id
    ]
    assert len(reports) == 1


def test_empty_early_exit_creates_no_report_and_changes_no_fsrs_data(tmp_path, monkeypatch):
    client, db = load_app(tmp_path, monkeypatch)
    collection_id = client.get("/api/dashboard").get_json()["collections"][0]["id"]
    sentences = [make_sentence(client, collection_id, index) for index in ("empty-a", "empty-b")]
    session_id = start_session(client, sentences)
    with db.get_db() as connection:
        before = {
            sentence["id"]: dict(connection.execute(
                "SELECT * FROM sentences WHERE id=?", (sentence["id"],)
            ).fetchone())
            for sentence in sentences
        }

    response = early_exit(
        client,
        session_id,
        sentences,
        draftAnswers=[
            {"sentenceId": sentences[0]["id"], "answerOrder": sentences[0]["correctOrder"]},
            {"sentenceId": sentences[1]["id"], "answerOrder": []},
        ],
    )
    assert response.status_code == 409
    assert response.get_json() == {
        "error": "当前还没有完成任何题目",
        "noCompletedItems": True,
        "completedCount": 0,
        "unansweredCount": 2,
    }
    assert client.get(f"/api/reports/{session_id}").status_code == 404
    assert all(row["id"] != session_id for row in client.get("/api/reports").get_json()["reports"])

    with db.get_db() as connection:
        session = connection.execute(
            "SELECT * FROM practice_sessions WHERE id=?", (session_id,)
        ).fetchone()
        assert session["completed_at"] is None
        assert session["completion_mode"] == "normal"
        assert connection.execute(
            "SELECT COUNT(*) n FROM review_events WHERE session_id=?", (session_id,)
        ).fetchone()["n"] == 0
        for sentence in sentences:
            after = dict(connection.execute(
                "SELECT * FROM sentences WHERE id=?", (sentence["id"],)
            ).fetchone())
            for field in FSRS_FIELDS:
                assert after[field] == before[sentence["id"]][field]
        drafts = connection.execute(
            "SELECT draft_answer_order_json FROM practice_items WHERE session_id=?",
            (session_id,),
        ).fetchall()
        assert all(row["draft_answer_order_json"] == "[]" for row in drafts)


def test_failed_early_exit_rolls_back_and_retry_completes_once(tmp_path, monkeypatch):
    client, db = load_app(tmp_path, monkeypatch)
    import app as app_module

    collection_id = client.get("/api/dashboard").get_json()["collections"][0]["id"]
    sentences = [make_sentence(client, collection_id, index) for index in ("retry-a", "retry-b")]
    session_id = start_session(client, sentences)
    for sentence in sentences:
        check_answer(client, session_id, sentence, correct=True)

    real_finalize = app_module._finalize_question
    calls = 0

    def fail_on_second_item(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError("simulated submission failure")
        return real_finalize(*args, **kwargs)

    monkeypatch.setattr(app_module, "_finalize_question", fail_on_second_item)
    failed = early_exit(client, session_id, sentences)
    assert failed.status_code == 500

    with db.get_db() as connection:
        assert connection.execute(
            "SELECT COUNT(*) n FROM review_events WHERE session_id=?", (session_id,)
        ).fetchone()["n"] == 0
        assert connection.execute(
            "SELECT COUNT(*) n FROM practice_items "
            "WHERE session_id=? AND finalized_at IS NOT NULL",
            (session_id,),
        ).fetchone()["n"] == 0
        assert connection.execute(
            "SELECT completed_at FROM practice_sessions WHERE id=?", (session_id,)
        ).fetchone()["completed_at"] is None

    monkeypatch.setattr(app_module, "_finalize_question", real_finalize)
    retried = early_exit(client, session_id, sentences)
    assert retried.status_code == 200
    assert retried.get_json()["completedCount"] == 2
    with db.get_db() as connection:
        assert connection.execute(
            "SELECT COUNT(*) n FROM review_events WHERE session_id=?", (session_id,)
        ).fetchone()["n"] == 2
    assert len([
        row for row in client.get("/api/reports").get_json()["reports"]
        if row["id"] == session_id
    ]) == 1


def test_normal_completion_metadata_and_flow_are_unchanged(tmp_path, monkeypatch):
    client, db = load_app(tmp_path, monkeypatch)
    collection_id = client.get("/api/dashboard").get_json()["collections"][0]["id"]
    sentence = make_sentence(client, collection_id, "normal")
    session_id = start_session(client, [sentence])
    check_answer(client, session_id, sentence, correct=True)

    completed = client.post(f"/api/practice/sessions/{session_id}/complete", json={})
    assert completed.status_code == 200
    assert completed.get_json()["completionMode"] == "normal"
    assert completed.get_json()["endedEarly"] is False
    assert completed.get_json()["completedCount"] == 1
    report = client.get(f"/api/reports/{session_id}").get_json()["report"]
    assert report["completionMode"] == "normal"
    assert report["endedEarly"] is False
    assert report["completedCount"] == 1
    with db.get_db() as connection:
        assert connection.execute(
            "SELECT COUNT(*) n FROM review_events WHERE session_id=?", (session_id,)
        ).fetchone()["n"] == 1


def test_completion_mode_migration_preserves_existing_report(tmp_path, monkeypatch):
    client, db = load_app(tmp_path, monkeypatch)
    collection_id = client.get("/api/dashboard").get_json()["collections"][0]["id"]
    sentence = make_sentence(client, collection_id, "migration")
    session_id = start_session(client, [sentence])
    check_answer(client, session_id, sentence, correct=True)
    assert client.post(f"/api/practice/sessions/{session_id}/complete", json={}).status_code == 200

    with db.get_db() as connection:
        before = {
            table: connection.execute(f"SELECT COUNT(*) n FROM {table}").fetchone()["n"]
            for table in ("sentences", "practice_sessions", "practice_items", "attempts", "review_events")
        }
        connection.execute("ALTER TABLE practice_sessions DROP COLUMN completion_mode")
        connection.execute(
            "DELETE FROM schema_migrations WHERE version='practice_completion_mode_v1'"
        )

    migrated_client, migrated_db = load_app(tmp_path, monkeypatch)
    with migrated_db.get_db() as connection:
        after = {
            table: connection.execute(f"SELECT COUNT(*) n FROM {table}").fetchone()["n"]
            for table in ("sentences", "practice_sessions", "practice_items", "attempts", "review_events")
        }
        assert after == before
        assert "completion_mode" in {
            row["name"] for row in connection.execute("PRAGMA table_info(practice_sessions)")
        }
        assert connection.execute(
            "SELECT COUNT(*) n FROM schema_migrations "
            "WHERE version='practice_completion_mode_v1'"
        ).fetchone()["n"] == 1
    report = migrated_client.get(f"/api/reports/{session_id}").get_json()["report"]
    assert report["completionMode"] == "normal"
    assert report["endedEarly"] is False
