import importlib
import json
import sqlite3
import uuid
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
    assert set(data) == {"chunks", "source", "sentenceFurigana"}
    assert "".join(x["text"] for x in data["chunks"]) == "こんにちは。"
    assert "".join(x["text"] for x in data["sentenceFurigana"]) == "こんにちは。"
    assert client.get("/api/settings").status_code == 404
    assert client.post("/api/settings/test", json={}).status_code == 404
    assert client.post("/api/sentences/organize", json={"chinese": [], "japanese": 123}).status_code == 400
    assert client.post("/api/sentences/organize", json={"chinese": "", "japanese": ""}).status_code == 400


def test_furigana_segments_plain_kana():
    from tokenizer import furigana_segments
    sentence = "これはペンです。"
    segments = furigana_segments(sentence)
    assert "".join(seg["text"] for seg in segments) == sentence
    assert all("ruby" not in seg for seg in segments)


def test_furigana_segments_with_kanji():
    from tokenizer import furigana_segments
    sentence = "電気が消えた。"
    segments = furigana_segments(sentence)
    assert "".join(seg["text"] for seg in segments) == sentence
    ruby_segs = [seg for seg in segments if seg.get("ruby")]
    assert ruby_segs
    denki = next((seg for seg in segments if seg["text"] == "電気"), None)
    assert denki is not None
    assert denki["ruby"] == "でんき"
    # 消えた may be split as 消え+た; 消 should get き if peeled, or whole ruby if not.
    kie = next((seg for seg in segments if "消" in seg["text"] and seg.get("ruby")), None)
    assert kie is not None


def test_furigana_segments_mixed_names_numbers_punctuation():
    from tokenizer import furigana_segments
    sentence = "田中さんは2024年、東京に行った。"
    segments = furigana_segments(sentence)
    assert "".join(seg["text"] for seg in segments) == sentence
    assert isinstance(segments, list)


def test_crud_practice_srs_report(tmp_path, monkeypatch):
    client = load_app(tmp_path, monkeypatch)
    collection = client.get("/api/dashboard").get_json()["collections"][0]["id"]
    chunks = client.post("/api/sentences/organize", json={"chinese":"我也去。", "japanese":"私も行きます。"}).get_json()["chunks"]
    created = client.post("/api/sentences", json={"collectionId":collection,"chinese":"我也去。","japanese":"私も行きます。","chunks":chunks,"correctOrder":[x["id"] for x in chunks]})
    assert created.status_code == 201
    sentence = created.get_json()["sentence"]
    practice = client.post("/api/practice/sessions", json={"sentenceIds":[sentence["id"]]}).get_json()
    wrong = client.post(f'/api/practice/sessions/{practice["sessionId"]}/attempts', json={"attemptId":str(uuid.uuid4()),"sentenceId":sentence["id"],"action":"check","answerOrder":list(reversed(sentence["correctOrder"]))})
    assert wrong.get_json()["status"] == "wrong"
    client.post(f'/api/practice/sessions/{practice["sessionId"]}/complete', json={})
    report = client.get(f'/api/reports/{practice["sessionId"]}').get_json()["report"]
    assert report["wrong"] == 1 and report["items"][0]["answerText"]
    refreshed = client.get(f'/api/sentences/{sentence["id"]}').get_json()["sentence"]
    assert refreshed["stability"] is not None and refreshed["difficulty"] is not None


def test_split_merge_validation_and_duplicate_ids():
    from tokenizer import validate_chunks
    original = "ここに猫がいて、そこに犬がいる。"
    chunks = [{"id":"a","text":"ここに"},{"id":"b","text":"猫がいて"},{"id":"c","text":"、"},{"id":"d","text":"そこに"},{"id":"e","text":"犬がいる"},{"id":"f","text":"。"}]
    assert validate_chunks(original, chunks)[0]
    split = chunks[:1]+[{"id":"b1","text":"猫が"},{"id":"b2","text":"いて"}]+chunks[2:]
    assert validate_chunks(original, split)[0]
    merged = split[:1]+[{"id":"m","text":"猫がいて"}]+split[3:]
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
        {"id":"first-ni", "text":"に"},
        {"id":"middle", "text":"猫"},
        {"id":"second-ni", "text":"に"},
    ]
    sentence = client.post("/api/sentences", json={
        "collectionId":collection, "chinese":"给猫", "japanese":"に猫に",
        "chunks":chunks, "correctOrder":[x["id"] for x in chunks],
    }).get_json()["sentence"]
    practice = client.post("/api/practice/sessions", json={"sentenceIds":[sentence["id"]]}).get_json()
    endpoint = f'/api/practice/sessions/{practice["sessionId"]}/attempts'
    first = client.post(endpoint, json={"attemptId":str(uuid.uuid4()), "sentenceId":sentence["id"], "action":"check", "answerOrder":["first-ni"]})
    assert first.get_json()["status"] == "wrong"
    final = client.post(endpoint, json={"attemptId":str(uuid.uuid4()), "sentenceId":sentence["id"], "action":"check", "answerOrder":sentence["correctOrder"]})
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
        {"id": "first-ni", "text": "に"},
        {"id": "middle", "text": "猫"},
        {"id": "second-ni", "text": "に"},
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
        "attemptId": str(uuid.uuid4()),
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
        "attemptId": str(uuid.uuid4()),
        "sentenceId": sentence["id"],
        "action": "check",
        "answerOrder": ["middle", "first-ni", "second-ni"],
    })
    assert wrong.get_json()["status"] == "wrong"


def test_resolve_limit():
    from app import _resolve_limit

    assert _resolve_limit(None, 5, "当前句集") == (5, "")
    assert _resolve_limit("all", 5, "当前句集") == (5, "")
    assert _resolve_limit(2, 5, "当前句集") == (2, "")
    assert _resolve_limit(5, 5, "当前句集") == (5, "")
    assert _resolve_limit(20, 3, "当前句集") == (3, "当前句集只有 3 句，已调整为全部")
    assert _resolve_limit(10, 2, "当前待复习") == (2, "当前待复习只有 2 句，已调整为全部")
    assert _resolve_limit(10, 2, "当前句集待复习") == (2, "当前句集待复习只有 2 句，已调整为全部")
    assert _resolve_limit("abc", 5, "当前句集") == (None, "题目数量必须是正整数")
    assert _resolve_limit([], 5, "当前句集") == (None, "题目数量必须是正整数")
    assert _resolve_limit(0, 5, "当前句集") == (1, "")
    assert _resolve_limit(-3, 5, "当前句集") == (1, "")
    assert _resolve_limit("4", 5, "当前句集") == (4, "")


def test_collection_count_is_clamped_and_random_scope_uses_all_sentences(tmp_path, monkeypatch):
    client = load_app(tmp_path, monkeypatch)
    collection = client.get("/api/dashboard").get_json()["collections"][0]["id"]
    for index in range(3):
        chunks = [{"id":f"x{index}", "text":str(index)}]
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
    created = client.post("/api/sentences", json={
        "collectionId": collection, "chinese": "旧分块", "japanese": "昔の分け方。",
        "chunks": saved_chunks, "correctOrder": ["old-a", "old-b"],
    })
    sentence_id = created.get_json()["sentence"]["id"]
    db.init_db()
    with sqlite3.connect(tmp_path / "japanese_sentence_review.sqlite3") as connection:
        assert connection.execute("SELECT key FROM settings WHERE key IN ('base_url','model','custom_params','api_key_encrypted')").fetchall() == []
        stored = json.loads(connection.execute("SELECT chunks_json FROM sentences WHERE id=?", (sentence_id,)).fetchone()[0])
    assert stored == saved_chunks


def _make_sentence(client, collection, chinese, japanese):
    chunks = client.post("/api/sentences/organize", json={"chinese": chinese, "japanese": japanese}).get_json()["chunks"]
    created = client.post("/api/sentences", json={
        "collectionId": collection,
        "chinese": chinese,
        "japanese": japanese,
        "chunks": chunks,
        "correctOrder": [x["id"] for x in chunks],
    })
    assert created.status_code == 201
    return created.get_json()["sentence"]


def _practice_once(client, sentence):
    practice = client.post("/api/practice/sessions", json={"sentenceIds": [sentence["id"]]}).get_json()
    client.post(
        f'/api/practice/sessions/{practice["sessionId"]}/attempts',
        json={"attemptId": str(uuid.uuid4()), "sentenceId": sentence["id"], "action": "check", "answerOrder": sentence["correctOrder"], "durationMs": 3000},
    )
    client.post(
        f'/api/practice/sessions/{practice["sessionId"]}/sentences/{sentence["id"]}/complete',
        json={},
    )
    return practice["sessionId"]


def test_delete_collection_cascade_clears_sentences_and_memory(tmp_path, monkeypatch):
    client = load_app(tmp_path, monkeypatch)
    import db
    default_id = client.get("/api/dashboard").get_json()["collections"][0]["id"]
    other = client.post("/api/collections", json={"name": "待删句集"})
    assert other.status_code == 201
    other_id = other.get_json()["id"]
    sentence = _make_sentence(client, other_id, "你好。", "こんにちは。")
    _practice_once(client, sentence)
    with db.get_db() as connection:
        assert connection.execute("SELECT COUNT(*) n FROM review_events WHERE sentence_id=?", (sentence["id"],)).fetchone()["n"] >= 1
        assert connection.execute("SELECT COUNT(*) n FROM attempts WHERE sentence_id=?", (sentence["id"],)).fetchone()["n"] >= 1

    res = client.delete(f"/api/collections/{other_id}?cascade=1")
    assert res.status_code == 200

    with db.get_db() as connection:
        assert connection.execute("SELECT COUNT(*) n FROM collections WHERE id=?", (other_id,)).fetchone()["n"] == 0
        assert connection.execute("SELECT COUNT(*) n FROM sentences WHERE id=?", (sentence["id"],)).fetchone()["n"] == 0
        assert connection.execute("SELECT COUNT(*) n FROM review_events WHERE sentence_id=?", (sentence["id"],)).fetchone()["n"] == 0
        assert connection.execute("SELECT COUNT(*) n FROM attempts WHERE sentence_id=?", (sentence["id"],)).fetchone()["n"] == 0
        assert connection.execute("SELECT COUNT(*) n FROM collections WHERE id=?", (default_id,)).fetchone()["n"] == 1


def test_delete_collection_nonempty_without_cascade_is_409(tmp_path, monkeypatch):
    client = load_app(tmp_path, monkeypatch)
    default_id = client.get("/api/dashboard").get_json()["collections"][0]["id"]
    other_id = client.post("/api/collections", json={"name": "非空句集"}).get_json()["id"]
    sentence = _make_sentence(client, other_id, "谢谢。", "ありがとう。")
    res = client.delete(f"/api/collections/{other_id}")
    assert res.status_code == 409
    assert "移动或删除" in res.get_json()["error"]
    still = client.get(f"/api/sentences/{sentence['id']}")
    assert still.status_code == 200
    assert client.get("/api/dashboard").get_json()
    names = {c["name"] for c in client.get("/api/dashboard").get_json()["collections"]}
    assert "非空句集" in names
    assert any(c["id"] == default_id for c in client.get("/api/dashboard").get_json()["collections"])


def test_delete_last_collection_rejected(tmp_path, monkeypatch):
    client = load_app(tmp_path, monkeypatch)
    only_id = client.get("/api/dashboard").get_json()["collections"][0]["id"]
    res = client.delete(f"/api/collections/{only_id}?cascade=1")
    assert res.status_code == 409
    assert "至少保留一个句集" in res.get_json()["error"]
    assert client.get("/api/dashboard").get_json()["collections"][0]["id"] == only_id


def test_move_sentences_between_collections(tmp_path, monkeypatch):
    client = load_app(tmp_path, monkeypatch)
    import db
    source_id = client.get("/api/dashboard").get_json()["collections"][0]["id"]
    target_id = client.post("/api/collections", json={"name": "目标句集"}).get_json()["id"]
    sentence = _make_sentence(client, source_id, "再见。", "さようなら。")
    _practice_once(client, sentence)
    before = client.get(f"/api/sentences/{sentence['id']}").get_json()["sentence"]
    with db.get_db() as connection:
        event_n = connection.execute("SELECT COUNT(*) n FROM review_events WHERE sentence_id=?", (sentence["id"],)).fetchone()["n"]
        attempt_n = connection.execute("SELECT COUNT(*) n FROM attempts WHERE sentence_id=?", (sentence["id"],)).fetchone()["n"]
    assert event_n >= 1 and attempt_n >= 1

    missing = client.post("/api/sentences/move", json={"sentenceIds": [sentence["id"]], "targetCollectionId": 99999})
    assert missing.status_code == 404
    empty = client.post("/api/sentences/move", json={"sentenceIds": [], "targetCollectionId": target_id})
    assert empty.status_code == 400

    moved = client.post("/api/sentences/move", json={"sentenceIds": [sentence["id"]], "targetCollectionId": target_id})
    assert moved.status_code == 200
    assert moved.get_json()["moved"] == 1

    after = client.get(f"/api/sentences/{sentence['id']}").get_json()["sentence"]
    assert after["collection_id"] == target_id
    for field in ("fsrs_state", "fsrs_step", "stability", "difficulty", "last_review_at", "next_review_at"):
        assert after[field] == before[field]
    with db.get_db() as connection:
        assert connection.execute("SELECT COUNT(*) n FROM review_events WHERE sentence_id=?", (sentence["id"],)).fetchone()["n"] == event_n
        assert connection.execute("SELECT COUNT(*) n FROM attempts WHERE sentence_id=?", (sentence["id"],)).fetchone()["n"] == attempt_n
        assert connection.execute("SELECT collection_id FROM sentences WHERE id=?", (sentence["id"],)).fetchone()["collection_id"] == target_id


def test_start_session_invalid_sentence_ids_returns_400(tmp_path, monkeypatch):
    client = load_app(tmp_path, monkeypatch)
    response = client.post("/api/practice/sessions", json={"sentenceIds": ["not-an-id"]})
    assert response.status_code == 400
    assert response.get_json()["error"] == "参数无效"


def test_record_attempt_invalid_sentence_id_returns_400(tmp_path, monkeypatch):
    client = load_app(tmp_path, monkeypatch)
    collection = client.get("/api/dashboard").get_json()["collections"][0]["id"]
    sentence = _make_sentence(client, collection, "你好。", "こんにちは。")
    practice = client.post("/api/practice/sessions", json={"sentenceIds": [sentence["id"]]})
    assert practice.status_code == 201
    session_id = practice.get_json()["sessionId"]
    response = client.post(
        f"/api/practice/sessions/{session_id}/attempts",
        json={"attemptId": str(uuid.uuid4()), "sentenceId": "bad", "action": "check", "answerOrder": []},
    )
    assert response.status_code == 400
    assert response.get_json()["error"] == "参数无效"
