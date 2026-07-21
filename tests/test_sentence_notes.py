import importlib
import json
import uuid
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
FSRS_FIELDS = (
    "fsrs_state", "fsrs_step", "stability", "difficulty",
    "last_review_at", "next_review_at", "fsrs_version",
)
CHUNK_FIELDS = (
    "chunks_json", "correct_order_json", "practice_structure_json",
    "chunk_source", "chunk_schema_version", "chunks_manually_edited",
    "furigana_json",
)
HISTORY_TABLES = ("practice_sessions", "practice_items", "attempts", "review_events")


def load_app(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("APP_SECRET", "test-secret")
    monkeypatch.setenv("SESSION_COOKIE_SECURE", "false")
    monkeypatch.setenv("TRUST_PROXY_COUNT", "0")
    import app
    import db
    importlib.reload(db)
    importlib.reload(app)
    flask_app = app.create_app({"TESTING": True, "FSRS_ENABLE_FUZZING": False})
    return flask_app.test_client(), db


def sentence_payload(client, collection_id, *, note=None):
    chinese = "与平时不同，偏偏今天迟到了。"
    japanese = "普段と違って、今日に限って遅刻しました。"
    organized = client.post(
        "/api/sentences/organize", json={"chinese": chinese, "japanese": japanese}
    ).get_json()
    payload = {
        "collectionId": collection_id,
        "chinese": chinese,
        "japanese": japanese,
        "chunks": organized["chunks"],
        "correctOrder": organized["correctOrder"],
        "practiceStructure": organized["practiceStructure"],
        "chunkSource": organized["source"],
    }
    if note is not None:
        payload["note"] = note
    return payload


def update_payload(sentence, *, note_marker=True, note=""):
    payload = {
        "collectionId": sentence["collection_id"],
        "chinese": sentence["chinese"],
        "japanese": sentence["japanese"],
        "chunks": sentence["chunks"],
        "correctOrder": sentence["correctOrder"],
        "practiceStructure": sentence["practiceStructure"],
        "chunkSource": sentence["chunkSource"],
        "chunksManuallyEdited": sentence["chunksManuallyEdited"],
    }
    if note_marker:
        payload["note"] = note
    return payload


def create_sentence(client, collection_id, *, note=None):
    response = client.post(
        "/api/sentences", json=sentence_payload(client, collection_id, note=note)
    )
    assert response.status_code == 201
    return response.get_json()["sentence"]


def history_snapshot(db_module):
    with db_module.get_db() as connection:
        return {
            table: [dict(row) for row in connection.execute(f"SELECT * FROM {table} ORDER BY rowid")]
            for table in HISTORY_TABLES
        }


def practice_once(client, sentence):
    practice = client.post(
        "/api/practice/sessions", json={"sentenceIds": [sentence["id"]]}
    ).get_json()
    assert practice["sentences"][0]["note"] == sentence["note"]
    attempt = client.post(
        f'/api/practice/sessions/{practice["sessionId"]}/attempts',
        json={
            "attemptId": str(uuid.uuid4()),
            "sentenceId": sentence["id"],
            "action": "check",
            "answerOrder": sentence["correctOrder"],
            "durationMs": 1500,
        },
    )
    assert attempt.status_code == 200
    complete = client.post(
        f'/api/practice/sessions/{practice["sessionId"]}/sentences/{sentence["id"]}/complete',
        json={},
    )
    assert complete.status_code == 200
    return practice["sessionId"]


def test_note_migration_is_idempotent_and_preserves_sentences_fsrs_and_history(tmp_path, monkeypatch):
    client, db = load_app(tmp_path, monkeypatch)
    collection_id = client.get("/api/dashboard").get_json()["collections"][0]["id"]
    sentence = create_sentence(client, collection_id)
    practice_once(client, sentence)

    with db.get_db() as connection:
        connection.execute("ALTER TABLE sentences DROP COLUMN note")
        connection.execute(
            "DELETE FROM schema_migrations WHERE version=?", (db.SENTENCE_NOTE_MIGRATION,)
        )
        sentence_before = dict(
            connection.execute("SELECT * FROM sentences WHERE id=?", (sentence["id"],)).fetchone()
        )
        counts_before = {
            table: connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in ("sentences", *HISTORY_TABLES)
        }
    history_before = history_snapshot(db)

    db.init_db(enable_fuzzing=False)
    db.init_db(enable_fuzzing=False)

    with db.get_db() as connection:
        columns = {row["name"]: dict(row) for row in connection.execute("PRAGMA table_info(sentences)")}
        assert columns["note"]["notnull"] == 1
        assert columns["note"]["dflt_value"] == "''"
        migrated = dict(
            connection.execute("SELECT * FROM sentences WHERE id=?", (sentence["id"],)).fetchone()
        )
        assert migrated.pop("note") == ""
        assert migrated == sentence_before
        assert {
            table: connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in ("sentences", *HISTORY_TABLES)
        } == counts_before
        assert connection.execute(
            "SELECT COUNT(*) FROM schema_migrations WHERE version=?",
            (db.SENTENCE_NOTE_MIGRATION,),
        ).fetchone()[0] == 1
    assert history_snapshot(db) == history_before


def test_sentence_note_create_read_edit_clear_and_legacy_requests(tmp_path, monkeypatch):
    client, _ = load_app(tmp_path, monkeypatch)
    collection_id = client.get("/api/dashboard").get_json()["collections"][0]["id"]
    sentence = create_sentence(
        client, collection_id, note="  に限って：与平日不同\n偏偏、特别相信  "
    )
    assert sentence["note"] == "に限って：与平日不同\n偏偏、特别相信"
    assert client.get(f'/api/sentences/{sentence["id"]}').get_json()["sentence"]["note"] == sentence["note"]
    listed = client.get("/api/sentences").get_json()["sentences"]
    assert next(item for item in listed if item["id"] == sentence["id"])["note"] == sentence["note"]

    changed = client.put(
        f'/api/sentences/{sentence["id"]}',
        json=update_payload(sentence, note="  新备注\n第二行  "),
    )
    assert changed.status_code == 200
    refreshed = client.get(f'/api/sentences/{sentence["id"]}').get_json()["sentence"]
    assert refreshed["note"] == "新备注\n第二行"

    cleared = client.put(
        f'/api/sentences/{sentence["id"]}', json=update_payload(refreshed, note=" \n ")
    )
    assert cleared.status_code == 200
    refreshed = client.get(f'/api/sentences/{sentence["id"]}').get_json()["sentence"]
    assert refreshed["note"] == ""

    set_again = client.put(
        f'/api/sentences/{sentence["id"]}', json=update_payload(refreshed, note="稍后清空")
    )
    assert set_again.status_code == 200
    refreshed = client.get(f'/api/sentences/{sentence["id"]}').get_json()["sentence"]
    legacy_update = client.put(
        f'/api/sentences/{sentence["id"]}',
        json=update_payload(refreshed, note_marker=False),
    )
    assert legacy_update.status_code == 200
    assert client.get(f'/api/sentences/{sentence["id"]}').get_json()["sentence"]["note"] == ""

    legacy_created = create_sentence(client, collection_id)
    assert legacy_created["note"] == ""


def test_sentence_note_validation_rejects_non_string_and_over_limit(tmp_path, monkeypatch):
    client, _ = load_app(tmp_path, monkeypatch)
    collection_id = client.get("/api/dashboard").get_json()["collections"][0]["id"]
    base = sentence_payload(client, collection_id)
    for invalid in ([], 123, {"text": "备注"}, "注" * 1001):
        response = client.post("/api/sentences", json={**base, "note": invalid})
        assert response.status_code == 400
        assert "备注" in response.get_json()["error"]

    sentence = create_sentence(client, collection_id, note="有效备注")
    for invalid in (False, "注" * 1001):
        response = client.put(
            f'/api/sentences/{sentence["id"]}',
            json={**update_payload(sentence), "note": invalid},
        )
        assert response.status_code == 400
        assert "备注" in response.get_json()["error"]


def test_batch_note_writes_multiple_empty_notes_and_cleans_outer_whitespace(tmp_path, monkeypatch):
    client, _ = load_app(tmp_path, monkeypatch)
    collection_id = client.get("/api/dashboard").get_json()["collections"][0]["id"]
    first = create_sentence(client, collection_id)
    second = create_sentence(client, collection_id)

    response = client.post(
        "/api/sentences/batch-note",
        json={
            "sentenceIds": [first["id"], second["id"], first["id"]],
            "note": "  に限って：与平日不同\n偏偏、特别相信  ",
        },
    )
    assert response.status_code == 200
    assert response.get_json() == {"ok": True, "updated": 2}
    expected = "に限って：与平日不同\n偏偏、特别相信"
    assert client.get(f'/api/sentences/{first["id"]}').get_json()["sentence"]["note"] == expected
    assert client.get(f'/api/sentences/{second["id"]}').get_json()["sentence"]["note"] == expected


def test_batch_note_overwrites_existing_notes_and_is_idempotent(tmp_path, monkeypatch):
    client, _ = load_app(tmp_path, monkeypatch)
    collection_id = client.get("/api/dashboard").get_json()["collections"][0]["id"]
    empty = create_sentence(client, collection_id)
    existing = create_sentence(client, collection_id, note="原备注")
    equal = create_sentence(client, collection_id, note="新备注")
    suffixed = create_sentence(client, collection_id, note="原备注\n新备注")
    ids = [empty["id"], existing["id"], equal["id"], suffixed["id"]]
    import app as app_module
    font_rebuilds = []
    monkeypatch.setattr(app_module, "schedule_font_rebuild", lambda: font_rebuilds.append(True))
    client.application.config["TESTING"] = False

    timestamps_before = {
        sentence_id: client.get(f"/api/sentences/{sentence_id}").get_json()["sentence"]["updated_at"]
        for sentence_id in ids
    }
    monkeypatch.setattr(app_module, "now_iso", lambda: "2099-01-01T00:00:00+00:00")

    first = client.post(
        "/api/sentences/batch-note",
        json={"sentenceIds": ids, "note": "新备注"},
    )
    assert first.status_code == 200
    assert first.get_json() == {"ok": True, "updated": 3}
    assert font_rebuilds == [True]
    for sentence_id in ids:
        assert client.get(f"/api/sentences/{sentence_id}").get_json()["sentence"]["note"] == "新备注"

    timestamps_after_first = {
        sentence_id: client.get(f"/api/sentences/{sentence_id}").get_json()["sentence"]["updated_at"]
        for sentence_id in ids
    }
    assert timestamps_after_first[equal["id"]] == timestamps_before[equal["id"]]
    for sentence_id in (empty["id"], existing["id"], suffixed["id"]):
        assert timestamps_after_first[sentence_id] == "2099-01-01T00:00:00+00:00"

    monkeypatch.setattr(app_module, "now_iso", lambda: "2099-02-02T00:00:00+00:00")
    repeated = client.post(
        "/api/sentences/batch-note",
        json={"sentenceIds": ids, "note": "新备注"},
    )
    assert repeated.status_code == 200
    assert repeated.get_json() == {"ok": True, "updated": 0}
    assert font_rebuilds == [True]
    for sentence_id in ids:
        refreshed = client.get(f"/api/sentences/{sentence_id}").get_json()["sentence"]
        assert refreshed["note"] == "新备注"
        assert refreshed["updated_at"] == timestamps_after_first[sentence_id]


def test_batch_note_rejects_invalid_body_ids_and_notes(tmp_path, monkeypatch):
    client, _ = load_app(tmp_path, monkeypatch)
    collection_id = client.get("/api/dashboard").get_json()["collections"][0]["id"]
    sentence = create_sentence(client, collection_id)

    for invalid_body in (None, [], "备注"):
        response = client.post("/api/sentences/batch-note", json=invalid_body)
        assert response.status_code == 400
        assert "JSON 对象" in response.get_json()["error"]

    for invalid_ids in (None, [], [0], [-1], ["1"], [True], [1.0], {"id": 1}):
        response = client.post(
            "/api/sentences/batch-note",
            json={"sentenceIds": invalid_ids, "note": "有效备注"},
        )
        assert response.status_code == 400
        assert "sentenceIds" in response.get_json()["error"]

    for invalid_note in (None, 123, [], {}, "", " \n ", "注" * 1001):
        response = client.post(
            "/api/sentences/batch-note",
            json={"sentenceIds": [sentence["id"]], "note": invalid_note},
        )
        assert response.status_code == 400
        assert "备注" in response.get_json()["error"]


def test_batch_note_missing_or_overflow_rolls_back_entire_batch(tmp_path, monkeypatch):
    client, _ = load_app(tmp_path, monkeypatch)
    collection_id = client.get("/api/dashboard").get_json()["collections"][0]["id"]
    existing = create_sentence(client, collection_id, note="原备注")
    empty = create_sentence(client, collection_id)

    missing = client.post(
        "/api/sentences/batch-note",
        json={"sentenceIds": [empty["id"], existing["id"], 999999], "note": "不应写入"},
    )
    assert missing.status_code == 404
    assert "整批未作修改" in missing.get_json()["error"]
    assert client.get(f'/api/sentences/{existing["id"]}').get_json()["sentence"]["note"] == "原备注"
    assert client.get(f'/api/sentences/{empty["id"]}').get_json()["sentence"]["note"] == ""

    full = create_sentence(client, collection_id, note="旧" * 1000)
    overwrite_short = client.post(
        "/api/sentences/batch-note",
        json={"sentenceIds": [empty["id"], full["id"]], "note": "短备注"},
    )
    assert overwrite_short.status_code == 200
    assert overwrite_short.get_json() == {"ok": True, "updated": 2}
    assert client.get(f'/api/sentences/{empty["id"]}').get_json()["sentence"]["note"] == "短备注"
    assert client.get(f'/api/sentences/{full["id"]}').get_json()["sentence"]["note"] == "短备注"


def test_batch_note_only_changes_note_timestamp_and_preserves_history_snapshots(tmp_path, monkeypatch):
    client, db = load_app(tmp_path, monkeypatch)
    collection_id = client.get("/api/dashboard").get_json()["collections"][0]["id"]
    sentence = create_sentence(client, collection_id, note="练习开始时的备注")
    session_id = practice_once(client, sentence)

    with db.get_db() as connection:
        before = dict(
            connection.execute("SELECT * FROM sentences WHERE id=?", (sentence["id"],)).fetchone()
        )
        item_snapshot_before = connection.execute(
            "SELECT sentence_snapshot_json FROM practice_items WHERE session_id=? AND sentence_id=?",
            (session_id, sentence["id"]),
        ).fetchone()[0]
    history_before = history_snapshot(db)

    import app as app_module
    monkeypatch.setattr(app_module, "now_iso", lambda: "2099-01-01T00:00:00+00:00")
    response = client.post(
        "/api/sentences/batch-note",
        json={"sentenceIds": [sentence["id"]], "note": "批量覆盖"},
    )
    assert response.status_code == 200
    assert response.get_json() == {"ok": True, "updated": 1}

    with db.get_db() as connection:
        after = dict(
            connection.execute("SELECT * FROM sentences WHERE id=?", (sentence["id"],)).fetchone()
        )
        assert after["note"] == "批量覆盖"
        assert after["updated_at"] == "2099-01-01T00:00:00+00:00"
        assert {
            field for field in before if before[field] != after[field]
        } == {"note", "updated_at"}
        assert all(after[field] == before[field] for field in (*CHUNK_FIELDS, *FSRS_FIELDS))
        assert connection.execute(
            "SELECT sentence_snapshot_json FROM practice_items WHERE session_id=? AND sentence_id=?",
            (session_id, sentence["id"]),
        ).fetchone()[0] == item_snapshot_before
        assert json.loads(item_snapshot_before)["note"] == "练习开始时的备注"
        attempt_snapshot = json.loads(
            connection.execute(
                "SELECT sentence_snapshot_json FROM attempts WHERE session_id=? AND sentence_id=?",
                (session_id, sentence["id"]),
            ).fetchone()[0]
        )
        assert attempt_snapshot["note"] == "练习开始时的备注"
    assert history_snapshot(db) == history_before

    new_session = client.post(
        "/api/practice/sessions", json={"sentenceIds": [sentence["id"]]}
    ).get_json()
    assert new_session["sentences"][0]["note"] == "批量覆盖"


def test_note_only_edit_preserves_chunks_fsrs_history_and_existing_session_snapshot(tmp_path, monkeypatch):
    client, db = load_app(tmp_path, monkeypatch)
    collection_id = client.get("/api/dashboard").get_json()["collections"][0]["id"]
    sentence = create_sentence(client, collection_id, note="练习开始时的备注")
    session_id = practice_once(client, sentence)

    with db.get_db() as connection:
        before = dict(
            connection.execute("SELECT * FROM sentences WHERE id=?", (sentence["id"],)).fetchone()
        )
        item_snapshot_before = connection.execute(
            "SELECT sentence_snapshot_json FROM practice_items WHERE session_id=? AND sentence_id=?",
            (session_id, sentence["id"]),
        ).fetchone()[0]
    history_before = history_snapshot(db)

    response = client.put(
        f'/api/sentences/{sentence["id"]}',
        json=update_payload(sentence, note="编辑后的备注"),
    )
    assert response.status_code == 200
    refreshed = client.get(f'/api/sentences/{sentence["id"]}').get_json()["sentence"]
    assert refreshed["note"] == "编辑后的备注"

    with db.get_db() as connection:
        after = dict(
            connection.execute("SELECT * FROM sentences WHERE id=?", (sentence["id"],)).fetchone()
        )
        assert all(after[field] == before[field] for field in (*CHUNK_FIELDS, *FSRS_FIELDS))
        assert connection.execute(
            "SELECT sentence_snapshot_json FROM practice_items WHERE session_id=? AND sentence_id=?",
            (session_id, sentence["id"]),
        ).fetchone()[0] == item_snapshot_before
        assert json.loads(item_snapshot_before)["note"] == "练习开始时的备注"
        attempt_snapshot = json.loads(
            connection.execute(
                "SELECT sentence_snapshot_json FROM attempts WHERE session_id=? AND sentence_id=?",
                (session_id, sentence["id"]),
            ).fetchone()[0]
        )
        assert attempt_snapshot["note"] == "练习开始时的备注"
    assert history_snapshot(db) == history_before

    new_session = client.post(
        "/api/practice/sessions", json={"sentenceIds": [sentence["id"]]}
    ).get_json()
    assert new_session["sentences"][0]["note"] == "编辑后的备注"


def test_legacy_snapshot_without_note_is_normalized_and_reported_as_empty(tmp_path, monkeypatch):
    client, db = load_app(tmp_path, monkeypatch)
    collection_id = client.get("/api/dashboard").get_json()["collections"][0]["id"]
    sentence = create_sentence(client, collection_id, note="当前备注")
    practice = client.post(
        "/api/practice/sessions", json={"sentenceIds": [sentence["id"]]}
    ).get_json()
    session_id = practice["sessionId"]

    with db.get_db() as connection:
        snapshot = json.loads(
            connection.execute(
                "SELECT sentence_snapshot_json FROM practice_items WHERE session_id=? AND sentence_id=?",
                (session_id, sentence["id"]),
            ).fetchone()[0]
        )
        snapshot.pop("note")
        connection.execute(
            "UPDATE practice_items SET sentence_snapshot_json=? WHERE session_id=? AND sentence_id=?",
            (json.dumps(snapshot, ensure_ascii=False), session_id, sentence["id"]),
        )

    attempt = client.post(
        f"/api/practice/sessions/{session_id}/attempts",
        json={
            "attemptId": str(uuid.uuid4()),
            "sentenceId": sentence["id"],
            "action": "check",
            "answerOrder": sentence["correctOrder"],
        },
    )
    assert attempt.status_code == 200
    assert client.post(
        f'/api/practice/sessions/{session_id}/sentences/{sentence["id"]}/complete', json={}
    ).status_code == 200
    assert client.post(f"/api/practice/sessions/{session_id}/complete", json={}).status_code == 200
    report = client.get(f"/api/reports/{session_id}").get_json()["report"]
    assert report["items"][0]["note"] == ""


def test_move_and_batch_rechunk_preserve_note(tmp_path, monkeypatch):
    client, _ = load_app(tmp_path, monkeypatch)
    source_id = client.get("/api/dashboard").get_json()["collections"][0]["id"]
    target_id = client.post("/api/collections", json={"name": "备注保留目标"}).get_json()["id"]
    sentence = create_sentence(client, source_id, note="不得丢失\n第二行")
    overwritten = client.post(
        "/api/sentences/batch-note",
        json={"sentenceIds": [sentence["id"]], "note": "批量覆盖内容"},
    )
    assert overwritten.status_code == 200
    expected_note = "批量覆盖内容"

    moved = client.post(
        "/api/sentences/move",
        json={"sentenceIds": [sentence["id"]], "targetCollectionId": target_id},
    )
    assert moved.status_code == 200
    assert client.get(f'/api/sentences/{sentence["id"]}').get_json()["sentence"]["note"] == expected_note

    rechunked = client.post(
        "/api/sentences/rechunk", json={"sentenceIds": [sentence["id"]]}
    )
    assert rechunked.status_code == 200
    assert client.get(f'/api/sentences/{sentence["id"]}').get_json()["sentence"]["note"] == expected_note


def test_note_frontend_form_preview_practice_and_responsive_contracts():
    source = (ROOT / "static" / "app.js").read_text(encoding="utf-8")
    styles = (ROOT / "static" / "styles.css").read_text(encoding="utf-8")

    form = source.split("function addForm", 1)[1].split("async function renderAdd", 1)[0]
    assert form.index('id="chinese"') < form.index('id="note"') < form.index('id="japanese"')
    assert 'maxlength="1000"' in form
    assert "备注（可选）" in form
    assert "に限って：与平日不同、偏偏、特别相信" in form
    assert 'class="form-column"' in form
    assert "note:$('#note').value" in source
    assert "if (note) note.value = '';" in source
    organize_flow = source.split("else if (action === 'organize')", 1)[1].split(
        "else if (action === 'select-chunk')", 1
    )[0]
    assert "#note" not in organize_flow

    preview = source.split("function renderPreview()", 1)[1].split(
        "async function renderLibrary", 1
    )[0]
    assert "const noteRow = note ?" in preview
    assert '${esc(note)}' in preview
    assert '${noteRow}<div><span>日语原句' in preview
    assert 'class="preview-note"' in preview

    practice = source.split("function renderPractice()", 1)[1].split(
        "function answerDetails", 1
    )[0]
    assert "const note = sentenceNote(s);" in practice
    assert "const noteCard = note ?" in practice
    assert '${esc(note)}' in practice
    assert '${esc(s.chinese)}' in practice
    assert "${note ? 'has-note' : 'single'}" in practice
    assert "learner-art" not in source
    assert "prompt-scene" not in source
    assert 'class="card speech"' not in source

    assert ".form-column{display:grid" in styles
    assert ".note-input{min-height:78px}" in styles
    assert ".preview-fields .preview-note{white-space:pre-wrap}" in styles
    assert ".practice-prompt.has-note{grid-template-columns:minmax(200px,280px) minmax(0,1fr)}" in styles
    assert ".practice-prompt.single{grid-template-columns:minmax(0,1fr)}" in styles
    assert ".practice-note-body{" in styles and "white-space:pre-wrap" in styles
    assert "overflow-wrap:anywhere" in styles
    assert "@media(max-width:800px)" in styles
    assert ".practice-prompt.has-note{grid-template-columns:minmax(0,1fr);gap:12px}" in styles
    assert ".learner-art" not in styles
    assert ".prompt-scene" not in styles
    assert ".speech" not in styles
