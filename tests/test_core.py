import importlib
import json
import sqlite3
import pytest


def load_app(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("APP_SECRET", "test-secret")
    monkeypatch.setenv("SESSION_COOKIE_SECURE", "false")
    monkeypatch.setenv("TRUST_PROXY_COUNT", "0")
    import db, app
    importlib.reload(db)
    importlib.reload(app)
    return app.create_app({"TESTING": True}).test_client()


def test_tokenizer_exact_duplicate_and_punctuation():
    from tokenizer import local_tokenize, validate_chunks
    sentence = "私は庭にいて、猫に水をあげた。"
    chunks = local_tokenize(sentence)
    assert "".join(x["text"] for x in chunks) == sentence
    assert len({x["id"] for x in chunks}) == len(chunks)
    assert any(x["text"] == "、" for x in chunks)
    assert any(x["text"] == "。" for x in chunks)
    assert validate_chunks(sentence, chunks)[0]


def test_organize_is_local_sudachi_only(tmp_path, monkeypatch):
    client = load_app(tmp_path, monkeypatch)
    response = client.post("/api/sentences/organize", json={"chinese":"你好", "japanese":"こんにちは。"})
    data = response.get_json()
    assert response.status_code == 200
    assert data["source"] == "sudachi"
    assert set(data) == {"chunks", "source"}
    assert "".join(x["text"] for x in data["chunks"]) == "こんにちは。"
    assert client.get("/api/settings").status_code == 404
    assert client.post("/api/settings/test", json={}).status_code == 404
    assert client.post("/api/sentences/organize", json={"chinese": [], "japanese": 123}).status_code == 400
    assert client.post("/api/sentences/organize", json={"chinese": "", "japanese": ""}).status_code == 400


def test_crud_practice_srs_report(tmp_path, monkeypatch):
    client = load_app(tmp_path, monkeypatch)
    collection = client.get("/api/dashboard").get_json()["collections"][0]["id"]
    chunks = client.post("/api/sentences/organize", json={"chinese":"我也去。", "japanese":"私も行きます。"}).get_json()["chunks"]
    created = client.post("/api/sentences", json={"collectionId":collection,"chinese":"我也去。","japanese":"私も行きます。","chunks":chunks,"correctOrder":[x["id"] for x in chunks],"kana":"わたしもいきます","romaji":"watashi mo ikimasu"})
    assert created.status_code == 201
    sentence = created.get_json()["sentence"]
    practice = client.post("/api/practice/sessions", json={"sentenceIds":[sentence["id"]]}).get_json()
    wrong = client.post(f'/api/practice/sessions/{practice["sessionId"]}/attempts', json={"sentenceId":sentence["id"],"action":"check","answerOrder":list(reversed(sentence["correctOrder"]))})
    assert wrong.get_json()["status"] == "wrong"
    client.post(f'/api/practice/sessions/{practice["sessionId"]}/complete', json={})
    report = client.get(f'/api/reports/{practice["sessionId"]}').get_json()["report"]
    assert report["wrong"] == 1 and report["items"][0]["answerText"]
    refreshed = client.get(f'/api/sentences/{sentence["id"]}').get_json()["sentence"]
    assert refreshed["correct_streak"] == 0 and refreshed["wrong_count"] == 1


def test_split_merge_validation_and_duplicate_ids():
    from tokenizer import validate_chunks
    original = "ここに猫がいて、そこに犬がいる。"
    chunks = [{"id":"a","text":"ここに","kana":""},{"id":"b","text":"猫がいて","kana":""},{"id":"c","text":"、","kana":""},{"id":"d","text":"そこに","kana":""},{"id":"e","text":"犬がいる","kana":""},{"id":"f","text":"。","kana":""}]
    assert validate_chunks(original, chunks)[0]
    split = chunks[:1]+[{"id":"b1","text":"猫が","kana":""},{"id":"b2","text":"いて","kana":""}]+chunks[2:]
    assert validate_chunks(original, split)[0]
    merged = split[:1]+[{"id":"m","text":"猫がいて","kana":""}]+split[3:]
    assert validate_chunks(original, merged)[0]
    merged[1]["id"] = "a"
    assert not validate_chunks(original, merged)[0]


def test_login_rate_limit_is_sqlite_backed(tmp_path, monkeypatch):
    monkeypatch.setenv("INIT_USERNAME", "owner")
    monkeypatch.setenv("INIT_PASSWORD", "correct-password")
    client = load_app(tmp_path, monkeypatch)
    for _ in range(5):
        response = client.post("/api/auth/login", json={"username":"owner", "password":"wrong"})
        assert response.status_code == 401
    locked = client.post("/api/auth/login", json={"username":"owner", "password":"correct-password"})
    assert locked.status_code == 429


# b 与 d 文字都为「に」，id 不同；按 text 判对时对调 id 仍应正确。
_MATCH_CHUNKS = [
    {"id": "a", "text": "私"},
    {"id": "b", "text": "に"},
    {"id": "c", "text": "は"},
    {"id": "d", "text": "に"},
]
_MATCH_CORRECT = ["a", "b", "c", "d"]


@pytest.mark.parametrize(
    "answer,expected",
    [
        ([], False),                              # 空答案
        (["a", "b"], False),                   # 只答开头部分
        (["a", "b", "c"], False),            # 少一个词块
        (["a", "b", "c", "d", "extra"], False),  # 多一个词块
        (["a", "c", "b", "d"], False),     # 真正顺序错误（「に」与「は」对调）
        (["a", "d", "c", "b"], True),      # 相同文字词块 ID 对调 → 按 text 仍正确
        (["a", "b", "c", "d"], True),      # 完整正确答案
    ],
)
def test_strict_answer_matching(answer, expected):
    from app import answers_match
    assert answers_match(answer, _MATCH_CORRECT, _MATCH_CHUNKS) is expected


def test_retry_current_replaces_attempt_and_final_result(tmp_path, monkeypatch):
    client = load_app(tmp_path, monkeypatch)
    collection = client.get("/api/dashboard").get_json()["collections"][0]["id"]
    chunks = [
        {"id":"first-ni", "text":"に", "kana":""},
        {"id":"middle", "text":"猫", "kana":""},
        {"id":"second-ni", "text":"に", "kana":""},
    ]
    sentence = client.post("/api/sentences", json={
        "collectionId":collection, "chinese":"给猫", "japanese":"に猫に",
        "chunks":chunks, "correctOrder":[x["id"] for x in chunks],
    }).get_json()["sentence"]
    practice = client.post("/api/practice/sessions", json={"sentenceIds":[sentence["id"]]}).get_json()
    endpoint = f'/api/practice/sessions/{practice["sessionId"]}/attempts'
    first = client.post(endpoint, json={"sentenceId":sentence["id"], "action":"check", "answerOrder":["first-ni"]})
    assert first.get_json()["status"] == "wrong"
    final = client.post(endpoint, json={"sentenceId":sentence["id"], "action":"check", "answerOrder":sentence["correctOrder"]})
    assert final.get_json()["status"] == "correct"
    client.post(f'/api/practice/sessions/{practice["sessionId"]}/complete', json={})
    report = client.get(f'/api/reports/{practice["sessionId"]}').get_json()["report"]
    assert report["correct"] == 1 and report["wrong"] == 0
    assert len(report["items"]) == 1 and report["items"][0]["status"] == "correct"


def test_duplicate_chunk_text_matching_via_record_attempt(tmp_path, monkeypatch):
    """Same-surface chunks with different ids: swapped instances still grade correct."""
    client = load_app(tmp_path, monkeypatch)
    collection = client.get("/api/dashboard").get_json()["collections"][0]["id"]
    chunks = [
        {"id": "first-ni", "text": "に", "kana": ""},
        {"id": "middle", "text": "猫", "kana": ""},
        {"id": "second-ni", "text": "に", "kana": ""},
    ]
    sentence = client.post("/api/sentences", json={
        "collectionId": collection,
        "chinese": "给猫",
        "japanese": "に猫に",
        "chunks": chunks,
        "correctOrder": [x["id"] for x in chunks],
    }).get_json()["sentence"]
    practice = client.post("/api/practice/sessions", json={"sentenceIds": [sentence["id"]]}).get_json()
    endpoint = f'/api/practice/sessions/{practice["sessionId"]}/attempts'

    # Id instances of the two 「に」 swapped; text sequence still に猫に.
    swapped = client.post(endpoint, json={
        "sentenceId": sentence["id"],
        "action": "check",
        "answerOrder": ["second-ni", "middle", "first-ni"],
    })
    body = swapped.get_json()
    assert swapped.status_code == 200
    assert body["status"] == "correct"
    assert body["correct"] is True

    # Real order error must still fail.
    wrong = client.post(endpoint, json={
        "sentenceId": sentence["id"],
        "action": "check",
        "answerOrder": ["middle", "first-ni", "second-ni"],
    })
    assert wrong.get_json()["status"] == "wrong"


def test_collection_count_is_clamped_and_random_scope_uses_all_sentences(tmp_path, monkeypatch):
    client = load_app(tmp_path, monkeypatch)
    collection = client.get("/api/dashboard").get_json()["collections"][0]["id"]
    for index in range(3):
        chunks = [{"id":f"x{index}", "text":str(index), "kana":""}]
        client.post("/api/sentences", json={"collectionId":collection, "chinese":str(index), "japanese":str(index), "chunks":chunks, "correctOrder":[f"x{index}"]})
    result = client.post("/api/practice/sessions", json={"scope":"collection", "collectionId":collection, "count":20}).get_json()
    assert len(result["sentences"]) == 3
    assert "已调整为全部" in result["notice"]


def test_due_session_count_limits_and_default_uses_all_due_sentences(tmp_path, monkeypatch):
    client = load_app(tmp_path, monkeypatch)
    collection = client.get("/api/dashboard").get_json()["collections"][0]["id"]
    sentence_ids = []
    for index in range(3):
        chunk_id = f"due-{index}"
        created = client.post("/api/sentences", json={
            "collectionId": collection,
            "chinese": f"到期句子 {index}",
            "japanese": f"文{index}",
            "chunks": [{"id": chunk_id, "text": f"文{index}"}],
            "correctOrder": [chunk_id],
        })
        assert created.status_code == 201
        sentence_ids.append(created.get_json()["sentence"]["id"])

    import db
    with db.get_db() as connection:
        for index, sentence_id in enumerate(sentence_ids):
            connection.execute(
                "UPDATE sentences SET next_review_at=? WHERE id=?",
                (f"2000-01-01T00:00:0{index}+00:00", sentence_id),
            )

    limited = client.post("/api/practice/sessions", json={"collectionId": collection, "count": 2})
    assert limited.status_code == 201
    assert [sentence["id"] for sentence in limited.get_json()["sentences"]] == sentence_ids[:2]

    all_due = client.post("/api/practice/sessions", json={"collectionId": collection})
    assert all_due.status_code == 201
    assert [sentence["id"] for sentence in all_due.get_json()["sentences"]] == sentence_ids


def test_migration_removes_only_legacy_remote_settings_and_keeps_saved_chunks(tmp_path, monkeypatch):
    client = load_app(tmp_path, monkeypatch)
    collection = client.get("/api/dashboard").get_json()["collections"][0]["id"]
    saved_chunks = [{"id": "old-a", "text": "昔の"}, {"id": "old-b", "text": "分け方。"}]
    import db

    stamp = db.now_iso()
    with db.get_db() as connection:
        connection.executemany(
            "INSERT INTO settings(key,value) VALUES(?,?)",
            [("base_url", "https://old.invalid"), ("model", "old"), ("custom_params", "{}"), ("api_key_encrypted", "secret")],
        )
        cursor = connection.execute(
            """INSERT INTO sentences(collection_id,chinese,japanese,chunks_json,correct_order_json,next_review_at,created_at,updated_at)
               VALUES(?,?,?,?,?,?,?,?)""",
            (collection, "旧分块", "昔の分け方。", json.dumps(saved_chunks, ensure_ascii=False), json.dumps(["old-a", "old-b"]), stamp, stamp, stamp),
        )
        sentence_id = cursor.lastrowid
    db.init_db()
    with sqlite3.connect(tmp_path / "japanese_sentence_review.sqlite3") as connection:
        assert connection.execute("SELECT key FROM settings WHERE key IN ('base_url','model','custom_params','api_key_encrypted')").fetchall() == []
        stored = json.loads(connection.execute("SELECT chunks_json FROM sentences WHERE id=?", (sentence_id,)).fetchone()[0])
    assert stored == saved_chunks
