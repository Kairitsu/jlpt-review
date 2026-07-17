import importlib
import json
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
    # Easy: correct on the first check with the explicit easy selection.
    assert attempt(client, session_id, sentences[3], correct=True).status_code == 200
    finish_question(client, session_id, sentences[3], easy=True)
    # Skipped: no FSRS rating.
    assert attempt(client, session_id, sentences[4], skip=True).status_code == 200
    finish_question(client, session_id, sentences[4])
    assert client.post(f"/api/practice/sessions/{session_id}/complete", json={}).status_code == 200

    report = client.get(f"/api/reports/{session_id}").get_json()["report"]
    expected = {"again": 1, "hard": 1, "good": 1, "easy": 1, "skipped": 1}
    assert report["ratingCounts"] == expected
    assert [item["rating"] for item in report["items"]] == [
        "again", "hard", "good", "easy", None,
    ]
    assert report["collection"] == {
        "id": collection["id"], "name": collection["name"], "available": 5,
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
