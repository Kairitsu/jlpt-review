import importlib
from datetime import datetime, timezone
from pathlib import Path


FROZEN_NOW = datetime(2026, 1, 1, 15, 30, tzinfo=timezone.utc)


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
    import app, db
    importlib.reload(db)
    importlib.reload(app)
    client = app.create_app({"TESTING": True}).test_client()
    monkeypatch.setattr(app, "datetime", FrozenDateTime)
    return client, db


def make_collection(client, name):
    response = client.post("/api/collections", json={"name": name})
    assert response.status_code == 201
    return response.get_json()["id"]


def make_sentence(client, collection_id, suffix):
    chunk_id = f"chunk-{suffix}"
    response = client.post("/api/sentences", json={
        "collectionId": collection_id,
        "chinese": f"中文 {suffix}",
        "japanese": f"日本語 {suffix}",
        "chunks": [{"id": chunk_id, "text": f"日本語 {suffix}"}],
        "correctOrder": [chunk_id],
    })
    assert response.status_code == 201
    return response.get_json()["sentence"]


def dashboard_collection(client, collection_id):
    return next(
        item for item in client.get("/api/dashboard").get_json()["collections"]
        if item["id"] == collection_id
    )


def add_review_event(db_module, sentence_id, reviewed_at):
    with db_module.get_db() as connection:
        card = connection.execute(
            """SELECT * FROM practice_cards
               WHERE sentence_id=? AND card_type='sentence_order' AND active=1""",
            (sentence_id,),
        ).fetchone()
        session_id = connection.execute(
            """INSERT INTO practice_sessions(source,sentence_ids_json,total,created_at)
               VALUES('selected',?,1,?)""",
            (f"[{sentence_id}]", reviewed_at),
        ).lastrowid
        connection.execute(
            """INSERT INTO review_events(
                 card_id,sentence_id,session_id,rating,reviewed_at,duration_ms,is_new,
                 fsrs_state_before,fsrs_state_after,fsrs_step_before,fsrs_step_after,
                 stability_before,stability_after,difficulty_before,difficulty_after,
                 next_review_before,next_review_after,fsrs_version,created_at
               ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                card["id"], sentence_id, session_id, 3, reviewed_at, 1000, 0,
                card["fsrs_state"], card["fsrs_state"],
                card["fsrs_step"], card["fsrs_step"],
                card["stability"], card["stability"],
                card["difficulty"], card["difficulty"],
                card["next_review_at"], card["next_review_at"],
                card["fsrs_version"], reviewed_at,
            ),
        )


def test_due_status_filters_sorts_scopes_and_matches_dashboard(tmp_path, monkeypatch):
    client, db = load_app(tmp_path, monkeypatch)
    first_collection = client.get("/api/dashboard").get_json()["collections"][0]["id"]
    other_collection = make_collection(client, "其他句集")
    empty_collection = make_collection(client, "空句集")
    oldest = make_sentence(client, first_collection, "oldest")
    newer = make_sentence(client, first_collection, "newer")
    future = make_sentence(client, first_collection, "future")
    other = make_sentence(client, other_collection, "other")

    with db.get_db() as connection:
        schedules = {
            oldest["id"]: "2025-12-20T00:00:00+00:00",
            newer["id"]: "2026-01-01T15:00:00+00:00",
            future["id"]: "2026-01-01T16:00:00+00:00",
            other["id"]: "2025-12-01T00:00:00+00:00",
        }
        connection.executemany(
            "UPDATE sentences SET next_review_at=? WHERE id=?",
            [(stamp, sentence_id) for sentence_id, stamp in schedules.items()],
        )
        connection.executemany(
            """UPDATE practice_cards SET next_review_at=?
               WHERE sentence_id=? AND card_type='sentence_order' AND active=1""",
            [(stamp, sentence_id) for sentence_id, stamp in schedules.items()],
        )

    detail = client.get(
        f"/api/collections/{first_collection}/study-status/due"
    ).get_json()
    assert detail["collection"]["id"] == first_collection
    assert detail["total"] == dashboard_collection(client, first_collection)["due"] == 2
    assert [item["id"] for item in detail["sentences"]] == [oldest["id"], newer["id"]]
    assert future["id"] not in [item["id"] for item in detail["sentences"]]
    assert other["id"] not in [item["id"] for item in detail["sentences"]]

    empty = client.get(
        f"/api/collections/{empty_collection}/study-status/due"
    ).get_json()
    assert empty["total"] == dashboard_collection(client, empty_collection)["due"] == 0
    assert empty["sentences"] == []


def test_today_status_uses_timezone_deduplicates_sorts_and_scopes(tmp_path, monkeypatch):
    client, db = load_app(tmp_path, monkeypatch)
    first_collection = client.get("/api/dashboard").get_json()["collections"][0]["id"]
    other_collection = make_collection(client, "另一个句集")
    empty_collection = make_collection(client, "没有学习记录")
    latest = make_sentence(client, first_collection, "latest")
    earlier = make_sentence(client, first_collection, "earlier")
    yesterday = make_sentence(client, first_collection, "yesterday")
    other = make_sentence(client, other_collection, "other")

    assert client.put(
        "/api/settings/timezone", json={"timezone": "Asia/Shanghai"}
    ).status_code == 200
    # Frozen UTC now is 2026-01-01 15:30. Shanghai's current natural day is
    # [2025-12-31 16:00, 2026-01-01 16:00) in UTC.
    add_review_event(db, latest["id"], "2025-12-31T16:10:00+00:00")
    add_review_event(db, latest["id"], "2026-01-01T15:20:00+00:00")
    add_review_event(db, earlier["id"], "2026-01-01T14:00:00+00:00")
    add_review_event(db, yesterday["id"], "2025-12-31T15:59:59+00:00")
    add_review_event(db, other["id"], "2026-01-01T13:00:00+00:00")

    detail = client.get(
        f"/api/collections/{first_collection}/study-status/today"
    ).get_json()
    assert detail["total"] == dashboard_collection(client, first_collection)["today"] == 2
    assert [item["id"] for item in detail["sentences"]] == [latest["id"], earlier["id"]]
    assert detail["sentences"][0]["today_last_review_at"] == "2026-01-01T15:20:00+00:00"
    assert len([item for item in detail["sentences"] if item["id"] == latest["id"]]) == 1
    assert yesterday["id"] not in [item["id"] for item in detail["sentences"]]
    assert other["id"] not in [item["id"] for item in detail["sentences"]]

    other_detail = client.get(
        f"/api/collections/{other_collection}/study-status/today"
    ).get_json()
    assert other_detail["total"] == dashboard_collection(client, other_collection)["today"] == 1
    assert [item["id"] for item in other_detail["sentences"]] == [other["id"]]

    empty = client.get(
        f"/api/collections/{empty_collection}/study-status/today"
    ).get_json()
    assert empty["total"] == dashboard_collection(client, empty_collection)["today"] == 0
    assert empty["sentences"] == []


def test_frontend_wires_status_buttons_routes_history_and_fab_scope():
    source = (Path(__file__).parents[1] / "static" / "app.js").read_text()
    assert 'class="metric metric-button" data-route="due"' in source
    assert 'class="metric metric-button" data-route="today"' in source
    assert "else if (name === 'due' || name === 'today')" in source
    assert "new Set(['due', 'today']).has(hashRoute)" in source
    assert "state.route === 'due' || state.route === 'today'" in source
    assert "history.state?.fromHome" in source
