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

    moved = client.post(
        "/api/sentences/move",
        json={"sentenceIds": [sentence["id"]], "targetCollectionId": target_id},
    )
    assert moved.status_code == 200
    assert client.get(f'/api/sentences/{sentence["id"]}').get_json()["sentence"]["note"] == sentence["note"]

    rechunked = client.post(
        "/api/sentences/rechunk", json={"sentenceIds": [sentence["id"]]}
    )
    assert rechunked.status_code == 200
    assert client.get(f'/api/sentences/{sentence["id"]}').get_json()["sentence"]["note"] == sentence["note"]


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
