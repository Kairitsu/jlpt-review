from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from fsrs_service import FSRS_VERSION, card_fields, new_card
from reading_cards import validate_reading_payload


def corpus_readings(db, *, exclude_sentence_id: int | None = None) -> list[dict[str, str]]:
    query = "SELECT id,analysis_json FROM sentences WHERE analysis_json IS NOT NULL"
    params: list[Any] = []
    if exclude_sentence_id is not None:
        query += " AND id<>?"
        params.append(exclude_sentence_id)
    result: list[dict[str, str]] = []
    for row in db.execute(query, params):
        try:
            analysis = json.loads(row["analysis_json"])
        except (TypeError, json.JSONDecodeError):
            continue
        for morpheme in analysis.get("morphemes") or []:
            if not isinstance(morpheme, dict):
                continue
            result.append(
                {
                    "surface": str(morpheme.get("text") or ""),
                    "reading": str(morpheme.get("reading") or ""),
                }
            )
    return result


def ensure_sentence_order_card(db, sentence_id: int, stamp: str):
    card = db.execute(
        """SELECT * FROM practice_cards
           WHERE sentence_id=? AND card_type='sentence_order' AND active=1""",
        (sentence_id,),
    ).fetchone()
    if card:
        return card
    sentence = db.execute(
        "SELECT * FROM sentences WHERE id=?", (sentence_id,)
    ).fetchone()
    if not sentence:
        raise ValueError("句子不存在")
    cursor = db.execute(
        """INSERT INTO practice_cards(
             sentence_id,card_type,card_key,payload_json,active,
             fsrs_state,fsrs_step,stability,difficulty,last_review_at,
             next_review_at,fsrs_version,created_at,updated_at
           ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            sentence_id,
            "sentence_order",
            "sentence_order",
            json.dumps({"schemaVersion": 1}, ensure_ascii=False),
            1,
            sentence["fsrs_state"],
            sentence["fsrs_step"],
            sentence["stability"],
            sentence["difficulty"],
            sentence["last_review_at"],
            sentence["next_review_at"],
            sentence["fsrs_version"],
            sentence["created_at"],
            stamp,
        ),
    )
    return db.execute(
        "SELECT * FROM practice_cards WHERE id=?", (cursor.lastrowid,)
    ).fetchone()


def _same_reading_meaning(old_payload: dict[str, Any], new_payload: dict[str, Any]) -> bool:
    old_target = old_payload.get("target")
    new_target = new_payload.get("target")
    if not isinstance(old_target, dict) or not isinstance(new_target, dict):
        return False
    return (
        old_target.get("start") == new_target.get("start")
        and old_target.get("end") == new_target.get("end")
        and old_target.get("surface") == new_target.get("surface")
        and old_payload.get("correctReading") == new_payload.get("correctReading")
    )


def reconcile_reading_cards(
    db,
    sentence_id: int,
    generated_cards: list[dict[str, Any]],
    *,
    stamp: str,
) -> dict[str, int]:
    """Preserve FSRS only when position, surface and contextual reading match."""
    sentence = db.execute(
        "SELECT japanese FROM sentences WHERE id=?", (sentence_id,)
    ).fetchone()
    if not sentence:
        raise ValueError("句子不存在")
    existing_rows = db.execute(
        """SELECT * FROM practice_cards
           WHERE sentence_id=? AND card_type='kanji_reading' AND active=1
           ORDER BY id""",
        (sentence_id,),
    ).fetchall()
    existing_by_key = {row["card_key"]: row for row in existing_rows}
    kept_ids: set[int] = set()
    stats = {"preserved": 0, "created": 0, "deactivated": 0}

    for generated in generated_cards:
        key = generated.get("cardKey")
        payload = generated.get("payload")
        valid, message = validate_reading_payload(sentence["japanese"], payload)
        if not valid:
            raise ValueError(message)
        old = existing_by_key.get(key)
        old_payload: dict[str, Any] = {}
        if old:
            try:
                old_payload = json.loads(old["payload_json"])
            except (TypeError, json.JSONDecodeError):
                old_payload = {}
        if old and _same_reading_meaning(old_payload, payload):
            kept_ids.add(old["id"])
            stats["preserved"] += 1
            continue
        if old:
            db.execute(
                "UPDATE practice_cards SET active=0,updated_at=? WHERE id=?",
                (stamp, old["id"]),
            )
            stats["deactivated"] += 1

        fields = card_fields(new_card(0, datetime.now(timezone.utc)))
        cursor = db.execute(
            """INSERT INTO practice_cards(
                 sentence_id,card_type,card_key,payload_json,active,
                 fsrs_state,fsrs_step,stability,difficulty,last_review_at,
                 next_review_at,fsrs_version,created_at,updated_at
               ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                sentence_id,
                "kanji_reading",
                key,
                json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
                1,
                fields["fsrs_state"],
                fields["fsrs_step"],
                fields["stability"],
                fields["difficulty"],
                fields["last_review_at"],
                fields["next_review_at"],
                FSRS_VERSION,
                stamp,
                stamp,
            ),
        )
        kept_ids.add(cursor.lastrowid)
        stats["created"] += 1

    for row in existing_rows:
        if row["id"] in kept_ids or not row["active"]:
            continue
        # A row already deactivated above is harmlessly filtered by active=1.
        changed = db.execute(
            """UPDATE practice_cards SET active=0,updated_at=?
               WHERE id=? AND active=1""",
            (stamp, row["id"]),
        ).rowcount
        stats["deactivated"] += changed
    return stats


def card_payload(row) -> dict[str, Any]:
    try:
        payload = json.loads(row["payload_json"])
    except (TypeError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def card_dict(card_row, sentence_row) -> dict[str, Any]:
    payload = card_payload(card_row)
    return {
        "id": card_row["id"],
        "cardId": card_row["id"],
        "sentenceId": card_row["sentence_id"],
        "cardType": card_row["card_type"],
        "cardKey": card_row["card_key"],
        "active": bool(card_row["active"]),
        "payload": payload,
        "fsrsState": card_row["fsrs_state"],
        "lastReviewAt": card_row["last_review_at"],
        "nextReviewAt": card_row["next_review_at"],
        "sentence": sentence_row,
    }
