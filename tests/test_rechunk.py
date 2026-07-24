import importlib
import json
import sqlite3
import uuid

import pytest


FSRS_FIELDS = (
    "fsrs_state", "fsrs_step", "stability", "difficulty",
    "last_review_at", "next_review_at", "fsrs_version",
)
HISTORY_TABLES = ("practice_sessions", "practice_items", "attempts", "review_events")


def load_app(tmp_path, monkeypatch, *, testing=True):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("APP_SECRET", "test-secret")
    monkeypatch.setenv("SESSION_COOKIE_SECURE", "false")
    monkeypatch.setenv("TRUST_PROXY_COUNT", "0")
    import app
    import db
    importlib.reload(db)
    importlib.reload(app)
    flask_app = app.create_app({"TESTING": testing, "FSRS_ENABLE_FUZZING": False})
    return flask_app.test_client(), app, db


def create_sentence(client, collection_id, chinese, japanese):
    organized = client.post(
        "/api/sentences/organize", json={"chinese": chinese, "japanese": japanese}
    ).get_json()
    response = client.post("/api/sentences", json={
        "collectionId": collection_id,
        "chinese": chinese,
        "japanese": japanese,
        "chunks": organized["chunks"],
        "correctOrder": organized["correctOrder"],
        "practiceStructure": organized["practiceStructure"],
        "chunkSource": organized["source"],
    })
    assert response.status_code == 201
    return response.get_json()["sentence"]


def make_chunks_manual(client, sentence):
    response = client.put(f'/api/sentences/{sentence["id"]}', json={
        "collectionId": sentence["collection_id"],
        "chinese": sentence["chinese"],
        "japanese": sentence["japanese"],
        "chunks": [{"id": f'manual-{sentence["id"]}', "text": sentence["japanese"]}],
        "correctOrder": [f'manual-{sentence["id"]}'],
    })
    assert response.status_code == 200
    return client.get(f'/api/sentences/{sentence["id"]}').get_json()["sentence"]


def practice_once(client, sentence):
    practice = client.post(
        "/api/practice/sessions", json={"sentenceIds": [sentence["id"]]}
    ).get_json()
    attempt = client.post(
        f'/api/practice/sessions/{practice["sessionId"]}/attempts',
        json={
            "attemptId": str(uuid.uuid4()),
            "sentenceId": sentence["id"],
            "action": "check",
            "answerOrder": sentence["correctOrder"],
            "durationMs": 1200,
        },
    )
    assert attempt.status_code == 200
    completed = client.post(
        f'/api/practice/sessions/{practice["sessionId"]}/sentences/{sentence["id"]}/complete',
        json={},
    )
    assert completed.status_code == 200


def table_snapshot(db_module, table):
    with db_module.get_db() as connection:
        return [dict(row) for row in connection.execute(f"SELECT * FROM {table} ORDER BY rowid")]


def raw_sentence_snapshot(db_module):
    with db_module.get_db() as connection:
        return [dict(row) for row in connection.execute("SELECT * FROM sentences ORDER BY id")]


def test_batch_rechunk_only_updates_selected_and_preserves_memory_and_history(tmp_path, monkeypatch):
    client, _, db = load_app(tmp_path, monkeypatch)
    collection_id = client.get("/api/dashboard").get_json()["collections"][0]["id"]
    first = make_chunks_manual(client, create_sentence(client, collection_id, "他读书。", "彼は本を読みます。"))
    second = make_chunks_manual(client, create_sentence(client, collection_id, "我学习日语。", "私は日本語を勉強します。"))
    untouched = make_chunks_manual(client, create_sentence(client, collection_id, "明天下雨。", "明日は雨が降ります。"))
    practice_once(client, first)
    with db.get_db() as connection:
        connection.execute(
            "UPDATE sentences SET updated_at='2000-01-01T00:00:00+00:00' WHERE id IN (?,?)",
            (first["id"], second["id"]),
        )

    first_before = client.get(f'/api/sentences/{first["id"]}').get_json()["sentence"]
    second_before = client.get(f'/api/sentences/{second["id"]}').get_json()["sentence"]
    untouched_before = client.get(f'/api/sentences/{untouched["id"]}').get_json()["sentence"]
    history_before = {table: table_snapshot(db, table) for table in HISTORY_TABLES}

    response = client.post(
        "/api/sentences/rechunk", json={"sentenceIds": [first["id"], second["id"]]}
    )
    assert response.status_code == 200
    response_data = response.get_json()
    assert response_data["ok"] is True
    assert response_data["updated"] == 2
    assert response_data["readingCardCount"] >= 0
    assert response_data["readingSkipCount"] >= 0

    from tokenizer import reconstruct_sentence, validate_practice_data
    for before in (first_before, second_before):
        after = client.get(f'/api/sentences/{before["id"]}').get_json()["sentence"]
        assert after["chunksManuallyEdited"] is False
        assert after["chunkSource"] == "kwja_tiny_phrase"
        assert after["chunkSchemaVersion"] == 3
        assert after["chunks"] != before["chunks"]
        assert after["chinese"] == before["chinese"]
        assert after["japanese"] == before["japanese"]
        assert after["collection_id"] == before["collection_id"]
        assert after["created_at"] == before["created_at"]
        assert after["updated_at"] != before["updated_at"]
        assert all(after[field] == before[field] for field in FSRS_FIELDS)
        assert "".join(segment["text"] for segment in after["furigana"]) == after["japanese"]
        assert validate_practice_data(
            after["japanese"], after["chunks"], after["practiceStructure"], after["correctOrder"]
        )[0]
        assert reconstruct_sentence(after["chunks"], after["practiceStructure"]) == after["japanese"]

    assert client.get(f'/api/sentences/{untouched["id"]}').get_json()["sentence"] == untouched_before
    assert {table: table_snapshot(db, table) for table in HISTORY_TABLES} == history_before


def test_batch_rechunk_rejects_bad_or_missing_ids_without_partial_update(tmp_path, monkeypatch):
    client, app_module, db = load_app(tmp_path, monkeypatch)
    collection_id = client.get("/api/dashboard").get_json()["collections"][0]["id"]
    sentence = make_chunks_manual(client, create_sentence(client, collection_id, "早上好。", "おはようございます。"))
    before = raw_sentence_snapshot(db)
    calls = []
    real_analyze = app_module.analyze_sentence
    monkeypatch.setattr(app_module, "analyze_sentence", lambda text: calls.append(text) or real_analyze(text))

    for payload in (
        {}, {"sentenceIds": []}, {"sentenceIds": [sentence["id"], "2"]},
        {"sentenceIds": [True]}, {"sentenceIds": [0]},
    ):
        assert client.post("/api/sentences/rechunk", json=payload).status_code == 400
    missing = client.post(
        "/api/sentences/rechunk", json={"sentenceIds": [sentence["id"], 999999]}
    )
    assert missing.status_code == 404
    assert "整批未作修改" in missing.get_json()["error"]
    assert calls == []
    assert raw_sentence_snapshot(db) == before


@pytest.mark.parametrize("failure_kind", ["analysis", "validation"])
def test_batch_rechunk_rolls_back_when_one_sentence_fails(
    tmp_path, monkeypatch, failure_kind
):
    client, app_module, db = load_app(tmp_path, monkeypatch)
    collection_id = client.get("/api/dashboard").get_json()["collections"][0]["id"]
    first = make_chunks_manual(client, create_sentence(client, collection_id, "一。", "一つ目の文です。"))
    second = make_chunks_manual(client, create_sentence(client, collection_id, "二。", "二つ目の文です。"))
    before = raw_sentence_snapshot(db)
    real_analyze = app_module.analyze_sentence

    def fail_second(text):
        if text != second["japanese"]:
            return real_analyze(text)
        if failure_kind == "analysis":
            raise RuntimeError("测试分析故障")
        invalid = real_analyze(text)
        invalid["structure"] = []
        return invalid

    monkeypatch.setattr(app_module, "analyze_sentence", fail_second)
    response = client.post(
        "/api/sentences/rechunk", json={"sentenceIds": [first["id"], second["id"]]}
    )
    assert response.status_code == 422
    assert "整批未作修改" in response.get_json()["error"]
    assert raw_sentence_snapshot(db) == before


def test_batch_rechunk_deduplicates_ids_and_rebuilds_fonts_once(tmp_path, monkeypatch):
    client, app_module, _ = load_app(tmp_path, monkeypatch, testing=False)
    collection_id = client.get("/api/dashboard").get_json()["collections"][0]["id"]
    sentence = create_sentence(client, collection_id, "谢谢。", "ありがとうございます。")
    analyze_calls = []
    font_calls = []
    real_analyze = app_module.analyze_sentence
    monkeypatch.setattr(
        app_module, "analyze_sentence", lambda text: analyze_calls.append(text) or real_analyze(text)
    )
    monkeypatch.setattr(app_module, "schedule_font_rebuild", lambda: font_calls.append(True))

    response = client.post(
        "/api/sentences/rechunk", json={"sentenceIds": [sentence["id"], sentence["id"]]}
    )
    assert response.status_code == 200
    assert response.get_json()["updated"] == 1
    assert analyze_calls == [sentence["japanese"]]
    assert font_calls == [True]
