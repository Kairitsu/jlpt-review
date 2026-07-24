import importlib
import json
import uuid

from kwja_analyzer import KWJAUnavailableError


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
    flask_app = app.create_app(
        {"TESTING": True, "FSRS_ENABLE_FUZZING": False}
    )
    return flask_app.test_client(), app, db


def analyzed_payload(client, collection_id, chinese, japanese):
    organized = client.post(
        "/api/sentences/organize",
        json={"chinese": chinese, "japanese": japanese},
    )
    assert organized.status_code == 200
    data = organized.get_json()
    return {
        "collectionId": collection_id,
        "chinese": chinese,
        "note": "上下文备注",
        "japanese": japanese,
        "chunks": data["chunks"],
        "correctOrder": data["correctOrder"],
        "practiceStructure": data["practiceStructure"],
        "chunkSource": data["source"],
        "readingCards": data["readingCards"],
    }


def create_sentence(client, collection_id, japanese="銀行に行く。"):
    payload = analyzed_payload(client, collection_id, "去银行。", japanese)
    response = client.post("/api/sentences", json=payload)
    assert response.status_code == 201
    return response.get_json()["sentence"], payload


def cards(client, sentence_id):
    response = client.get(f"/api/sentences/{sentence_id}/cards")
    assert response.status_code == 200
    return response.get_json()["cards"]


def card_row(db_module, card_id):
    with db_module.get_db() as connection:
        return dict(
            connection.execute(
                "SELECT * FROM practice_cards WHERE id=?", (card_id,)
            ).fetchone()
        )


def complete_reading_card(client, card):
    session = client.post(
        "/api/practice/sessions",
        json={"cardType": "kanji_reading", "cardIds": [card["id"]]},
    ).get_json()
    option_id = card["payload"]["correctOptionId"]
    attempt = client.post(
        f'/api/practice/sessions/{session["sessionId"]}/attempts',
        json={
            "attemptId": str(uuid.uuid4()),
            "cardId": card["id"],
            "sentenceId": card["sentenceId"],
            "action": "check",
            "answer": {
                "type": "kanji_reading",
                "selectedOptionId": option_id,
            },
        },
    )
    assert attempt.status_code == 200
    assert attempt.get_json()["correct"] is True
    finalized = client.post(
        f'/api/practice/sessions/{session["sessionId"]}/cards/{card["id"]}/complete',
        json={},
    )
    assert finalized.status_code == 200
    completed = client.post(
        f'/api/practice/sessions/{session["sessionId"]}/complete', json={}
    )
    assert completed.status_code == 200
    return session["sessionId"]


def test_reading_and_sentence_order_cards_have_independent_fsrs(tmp_path, monkeypatch):
    client, _, db_module = load_app(tmp_path, monkeypatch)
    collection_id = client.get("/api/dashboard").get_json()["collections"][0]["id"]
    sentence, _ = create_sentence(client, collection_id)
    all_cards = cards(client, sentence["id"])
    order = next(card for card in all_cards if card["cardType"] == "sentence_order")
    reading = next(card for card in all_cards if card["cardType"] == "kanji_reading")
    order_before = card_row(db_module, order["id"])
    reading_before = card_row(db_module, reading["id"])

    complete_reading_card(client, reading)

    order_after = card_row(db_module, order["id"])
    reading_after = card_row(db_module, reading["id"])
    assert all(order_after[field] == order_before[field] for field in FSRS_FIELDS)
    assert any(
        reading_after[field] != reading_before[field]
        for field in ("stability", "difficulty", "last_review_at", "next_review_at")
    )
    with db_module.get_db() as connection:
        sentence_mirror = connection.execute(
            "SELECT * FROM sentences WHERE id=?", (sentence["id"],)
        ).fetchone()
        events = connection.execute(
            "SELECT card_id FROM review_events ORDER BY id"
        ).fetchall()
    assert all(
        sentence_mirror[field] == order_after[field] for field in FSRS_FIELDS
    )
    assert [event["card_id"] for event in events] == [reading["id"]]


def test_daily_limits_and_completed_counts_are_separate_by_card_type(
    tmp_path, monkeypatch
):
    client, _, _ = load_app(tmp_path, monkeypatch)
    collection_id = client.get("/api/dashboard").get_json()["collections"][0]["id"]
    sentence, _ = create_sentence(
        client, collection_id, "銀行で日本語を勉強する。"
    )
    all_cards = cards(client, sentence["id"])
    order = next(card for card in all_cards if card["cardType"] == "sentence_order")
    readings = [
        card for card in all_cards if card["cardType"] == "kanji_reading"
    ]
    assert len(readings) >= 2
    saved = client.put(
        "/api/settings/daily-plan",
        json={
            "dailyAutoReviewLimit": 1,
            "dailyKanjiReadingReviewLimit": 2,
        },
    )
    assert saved.status_code == 200
    initial = client.get("/api/dashboard").get_json()
    assert initial["availableAutoReviewCount"] == 1
    assert initial["kanjiReading"]["availableReviewCount"] == 2

    order_session = client.post(
        "/api/practice/sessions",
        json={"cardType": "sentence_order", "cardIds": [order["id"]]},
    ).get_json()
    checked = client.post(
        f'/api/practice/sessions/{order_session["sessionId"]}/attempts',
        json={
            "attemptId": str(uuid.uuid4()),
            "cardId": order["id"],
            "sentenceId": sentence["id"],
            "action": "check",
            "answer": {
                "type": "sentence_order",
                "orderedChunkIds": sentence["correctOrder"],
            },
        },
    )
    assert checked.status_code == 200
    assert client.post(
        f'/api/practice/sessions/{order_session["sessionId"]}/cards/{order["id"]}/complete',
        json={},
    ).status_code == 200
    assert client.post(
        f'/api/practice/sessions/{order_session["sessionId"]}/complete', json={}
    ).status_code == 200
    after_order = client.get("/api/dashboard").get_json()
    assert after_order["completedToday"] == 1
    assert after_order["remainingAutoReviewQuota"] == 0
    assert after_order["kanjiReading"]["completedToday"] == 0
    assert after_order["kanjiReading"]["remainingQuota"] == 2

    complete_reading_card(client, readings[0])
    after_reading = client.get("/api/dashboard").get_json()
    assert after_reading["completedToday"] == 1
    assert after_reading["remainingAutoReviewQuota"] == 0
    assert after_reading["kanjiReading"]["completedToday"] == 1
    assert after_reading["kanjiReading"]["remainingQuota"] == 1


def test_edit_preserves_same_reading_but_deactivates_changed_target(tmp_path, monkeypatch):
    client, _, db_module = load_app(tmp_path, monkeypatch)
    collection_id = client.get("/api/dashboard").get_json()["collections"][0]["id"]
    sentence, original_payload = create_sentence(client, collection_id)
    original_cards = cards(client, sentence["id"])
    order = next(card for card in original_cards if card["cardType"] == "sentence_order")
    reading = next(card for card in original_cards if card["cardType"] == "kanji_reading")
    complete_reading_card(client, reading)
    reviewed = card_row(db_module, reading["id"])

    unchanged = client.put(
        f'/api/sentences/{sentence["id"]}', json=original_payload
    )
    assert unchanged.status_code == 200
    unchanged_reading = next(
        card
        for card in cards(client, sentence["id"])
        if card["cardType"] == "kanji_reading" and card["active"]
    )
    assert unchanged_reading["id"] == reading["id"]
    assert all(
        card_row(db_module, unchanged_reading["id"])[field] == reviewed[field]
        for field in FSRS_FIELDS
    )

    changed_payload = analyzed_payload(
        client, collection_id, "去东京。", "東京に行く。"
    )
    changed = client.put(
        f'/api/sentences/{sentence["id"]}', json=changed_payload
    )
    assert changed.status_code == 200
    changed_cards = cards(client, sentence["id"])
    old = next(card for card in changed_cards if card["id"] == reading["id"])
    new = next(
        card
        for card in changed_cards
        if card["cardType"] == "kanji_reading" and card["active"]
    )
    assert old["active"] is False
    assert new["id"] != reading["id"]
    assert new["payload"]["target"]["surface"] == "東京"
    assert next(
        card for card in changed_cards if card["cardType"] == "sentence_order"
    )["id"] == order["id"]


def test_reading_report_is_snapshot_and_survives_live_card_edits(tmp_path, monkeypatch):
    client, _, db_module = load_app(tmp_path, monkeypatch)
    collection_id = client.get("/api/dashboard").get_json()["collections"][0]["id"]
    sentence, _ = create_sentence(client, collection_id)
    reading = next(
        card
        for card in cards(client, sentence["id"])
        if card["cardType"] == "kanji_reading"
    )
    report_id = complete_reading_card(client, reading)
    before = client.get(f"/api/reports/{report_id}").get_json()["report"]

    with db_module.get_db() as connection:
        mutated = dict(reading["payload"])
        mutated["correctReading"] = "こわれた"
        connection.execute(
            "UPDATE practice_cards SET payload_json=? WHERE id=?",
            (json.dumps(mutated, ensure_ascii=False), reading["id"]),
        )
    after = client.get(f"/api/reports/{report_id}").get_json()["report"]
    assert after == before
    item = after["items"][0]
    assert item["cardType"] == "kanji_reading"
    assert item["correctReading"] == reading["payload"]["correctReading"]
    assert item["target"]["surface"] == "銀行"
    assert len(item["options"]) == 4
    assert item["note"] == "上下文备注"


def test_unavailable_kwja_does_not_overwrite_existing_sentence(tmp_path, monkeypatch):
    client, app_module, db_module = load_app(tmp_path, monkeypatch)
    collection_id = client.get("/api/dashboard").get_json()["collections"][0]["id"]
    sentence, payload = create_sentence(client, collection_id)
    with db_module.get_db() as connection:
        before = dict(
            connection.execute(
                "SELECT * FROM sentences WHERE id=?", (sentence["id"],)
            ).fetchone()
        )
        cards_before = [
            dict(row)
            for row in connection.execute(
                "SELECT * FROM practice_cards WHERE sentence_id=? ORDER BY id",
                (sentence["id"],),
            )
        ]
    monkeypatch.setattr(
        app_module,
        "analyze_sentence",
        lambda text: (_ for _ in ()).throw(
            KWJAUnavailableError("KWJA 服务不可用")
        ),
    )
    response = client.put(f'/api/sentences/{sentence["id"]}', json=payload)
    assert response.status_code == 503
    assert response.get_json()["analyzerUnavailable"] is True
    with db_module.get_db() as connection:
        after = dict(
            connection.execute(
                "SELECT * FROM sentences WHERE id=?", (sentence["id"],)
            ).fetchone()
        )
        cards_after = [
            dict(row)
            for row in connection.execute(
                "SELECT * FROM practice_cards WHERE sentence_id=? ORDER BY id",
                (sentence["id"],),
            )
        ]
    assert after == before
    assert cards_after == cards_before
