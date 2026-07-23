from __future__ import annotations

import hashlib
import hmac
import json
import math
import os
import secrets
import sqlite3
import time
from datetime import datetime, timedelta, timezone

from flask import Flask, jsonify, request, send_from_directory, session
from werkzeug.middleware.proxy_fix import ProxyFix

from auth import authed, clear, configured as auth_configured, fail, keys, lock_remaining
from db import get_db, init_db, json_load, now_iso, set_setting, setting
from fsrs import Rating
from fsrs_service import (
    DESIRED_RETENTION,
    FSRS_VERSION,
    MAXIMUM_INTERVAL_DAYS,
    RATING_POLICY_VERSION,
    RATING_LABELS_ZH,
    RATING_NAMES,
    attempt_facts,
    card_fields,
    determine_fsrs_rating,
    new_card,
    retrievability,
    review as fsrs_review,
)
from font_active import (
    active_dir,
    ensure_active_fonts,
    faces_css_text,
    safe_font_filename,
    schedule_font_rebuild,
    status as font_status,
)
from memory import (
    is_valid_timezone,
    local_date,
    local_date_utc_bounds,
    local_day_utc_bounds,
    parse_iso,
)
from security import hash_password, verify_password
from tokenizer import (
    CHUNK_SCHEMA_VERSION,
    TOKENIZER_NAME,
    analyze_sentence,
    furigana_segments,
    structure_from_manual_chunks,
    validate_practice_data,
)

MAX_SENTENCE_NOTE_LENGTH = 1000
DAILY_AUTO_REVIEW_LIMIT_KEY = "daily_auto_review_limit"
DEFAULT_DAILY_AUTO_REVIEW_LIMIT = 50
MIN_DAILY_AUTO_REVIEW_LIMIT = 1
MAX_DAILY_AUTO_REVIEW_LIMIT = 500


def truthy(name: str, default=False):
    return os.environ.get(name, str(default)).lower() in {"1", "true", "yes", "on"}


def _resolve_limit(requested, available, subject):
    """返回 (limit, notice) 或 (None, error_message)。"""
    if requested in (None, "all"):
        return available, ""
    try:
        limit = max(1, int(requested))
    except (TypeError, ValueError):
        return None, "题目数量必须是正整数"
    notice = ""
    if limit >= available:
        if limit > available:
            notice = f"{subject}只有 {available} 句，已调整为全部"
        limit = available
    return limit, notice


def _hard_delete_sentences(db, sentence_ids: list[int]) -> None:
    """Hard-delete sentences; FSRS history cascades through foreign keys."""
    if not sentence_ids:
        return
    placeholders = ",".join("?" for _ in sentence_ids)
    db.execute(f"DELETE FROM sentences WHERE id IN ({placeholders})", sentence_ids)


def sentence_dict(row):
    data = dict(row)
    note = data.get("note", "")
    data["note"] = note if isinstance(note, str) else ""
    data["chunks"] = json_load(data.pop("chunks_json"), [])
    data["correctOrder"] = json_load(data.pop("correct_order_json"), [])
    data["practiceStructure"] = json_load(data.pop("practice_structure_json", "[]"), [])
    data["chunkSource"] = data.pop("chunk_source", "legacy")
    data["chunkSchemaVersion"] = int(data.pop("chunk_schema_version", 1) or 1)
    data["chunksManuallyEdited"] = bool(data.pop("chunks_manually_edited", 0))
    data["furigana"] = json_load(data.pop("furigana_json", "[]"), [])
    return data


def sentence_snapshot(row):
    item = sentence_dict(row)
    snapshot = {
        key: item[key]
        for key in (
            "id", "chinese", "note", "japanese", "chunks", "correctOrder",
            "practiceStructure", "chunkSource", "chunkSchemaVersion",
            "chunksManuallyEdited", "furigana",
        )
    }
    # Keep the report tied to the collection that produced it even if the
    # sentence is moved later. Older snapshots are resolved from live rows.
    snapshot["collectionId"] = item["collection_id"]
    return snapshot


def snapshot_dict(value):
    """Read current and legacy sentence snapshots through one safe contract."""
    snapshot = json_load(value, {})
    if not isinstance(snapshot, dict) or not snapshot:
        return {}
    note = snapshot.get("note", "")
    snapshot["note"] = note if isinstance(note, str) else ""
    return snapshot


def _empty_rating_counts():
    return {"again": 0, "hard": 0, "good": 0, "easy": 0, "skipped": 0}


def _rating_counts(rows):
    """Summarize finalized practice items from persisted FSRS ratings."""
    counts = _empty_rating_counts()
    for row in rows:
        rating_value = row["fsrs_rating"]
        if rating_value:
            rating = Rating(rating_value)
            counts[RATING_NAMES[rating]] += 1
        elif row["final_status"] == "skipped":
            counts["skipped"] += 1
    return counts


def _session_completion_metadata(row):
    """Return stable report metadata for normal and early-exit submissions."""
    completion_mode = row["completion_mode"] or "normal"
    completed_count = sum(int(row[key] or 0) for key in ("correct", "wrong", "skipped"))
    return {
        "completionMode": completion_mode,
        "endedEarly": completion_mode == "early_exit",
        "plannedCount": int(row["total"] or 0),
        "completedCount": completed_count,
        "unansweredCount": int(row["unanswered"] or 0),
    }


def _report_collection(db, item_rows):
    """Resolve a report's collection without consulting the UI selection.

    New attempts carry the original collection in their snapshot. For legacy
    attempts, fall back to the current collection of the report's surviving
    sentences. The most frequent collection wins, with report order breaking
    ties so mixed-selection reports stay deterministic.
    """
    snapshot_ids = []
    for row in item_rows:
        snapshot = snapshot_dict(row["sentence_snapshot_json"])
        try:
            collection_id = int(snapshot.get("collectionId"))
        except (TypeError, ValueError):
            continue
        if collection_id > 0:
            snapshot_ids.append(collection_id)

    def most_common(values):
        frequencies = {}
        for value in values:
            frequencies[value] = frequencies.get(value, 0) + 1
        return max(frequencies, key=frequencies.get) if frequencies else None

    collection_id = most_common(snapshot_ids)
    collection = db.execute(
        "SELECT id,name FROM collections WHERE id=?", (collection_id,)
    ).fetchone() if collection_id else None

    if not collection:
        live_ids = []
        for row in item_rows:
            live = db.execute(
                "SELECT collection_id FROM sentences WHERE id=?", (row["sentence_id"],)
            ).fetchone()
            if live:
                live_ids.append(live["collection_id"])
        collection_id = most_common(live_ids)
        collection = db.execute(
            "SELECT id,name FROM collections WHERE id=?", (collection_id,)
        ).fetchone() if collection_id else None

    if not collection:
        return None
    available = db.execute(
        "SELECT COUNT(*) n FROM sentences WHERE collection_id=?", (collection["id"],)
    ).fetchone()["n"]
    due_count = _due_sentence_count(
        db, _study_status_context(db), collection_id=collection["id"]
    )
    return {
        "id": collection["id"],
        "name": collection["name"],
        "available": available,
        "dueCount": due_count,
    }


def answers_match(answer, correct, chunks):
    """Compare only learner-sortable chunk order by text, never fixed elements.

    Duplicate texts (e.g. two 「し」 with different ids) match when placed in the
    right positions even if the specific id instances are swapped. Punctuation
    and whitespace are absent from ``chunks`` by schema and cannot be submitted.
    """
    if not isinstance(answer, list) or not isinstance(correct, list):
        return False
    if len(answer) != len(correct):
        return False
    by_id = {
        item["id"]: item.get("text")
        for item in (chunks or [])
        if isinstance(item, dict) and isinstance(item.get("id"), str)
    }

    def to_texts(order):
        texts = []
        for chunk_id in order:
            if not isinstance(chunk_id, str):
                return None
            text = by_id.get(chunk_id)
            if not isinstance(text, str):
                return None
            texts.append(text)
        return texts

    answer_texts = to_texts(answer)
    correct_texts = to_texts(correct)
    if answer_texts is None or correct_texts is None:
        return False
    return answer_texts == correct_texts


def user_timezone(db) -> str:
    """Configured IANA timezone, or "" to fall back to the server's local timezone."""
    return setting(db, "user_timezone", "")


def daily_auto_review_limit(db) -> int:
    """Return the validated stored limit, falling back safely for legacy data."""
    raw = setting(db, DAILY_AUTO_REVIEW_LIMIT_KEY, str(DEFAULT_DAILY_AUTO_REVIEW_LIMIT))
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return DEFAULT_DAILY_AUTO_REVIEW_LIMIT
    if not MIN_DAILY_AUTO_REVIEW_LIMIT <= value <= MAX_DAILY_AUTO_REVIEW_LIMIT:
        return DEFAULT_DAILY_AUTO_REVIEW_LIMIT
    return value


_STATS_TIMELINE_DAYS = (
    (-4, "4天前"),
    (-3, "3天前"),
    (-2, "前天"),
    (-1, "昨天"),
    (0, "今天"),
)
_STATS_UPCOMING_DAYS = (
    (1, "明天"),
    (2, "后天"),
    (3, "3天后"),
)
_STATS_WEEKDAYS = ("星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日")
_STATS_RATINGS = (
    (Rating.Again, "forgotten", "忘记"),
    (Rating.Hard, "uncertain", "模糊"),
    (Rating.Good, "recognized", "认识"),
    (Rating.Easy, "mastered", "轻松掌握"),
)
_MEMORY_MASTERY_GROUPS = (
    ("veryStrong", "95%–100%", "记忆非常稳固"),
    ("strong", "90%–不足 95%", "当前较为稳固"),
    ("atRisk", "80%–不足 90%", "已有一定遗忘风险"),
    ("priority", "低于 80%", "建议优先复习"),
)


def _empty_daily_learning() -> dict:
    return {
        "completedCount": 0,
        "newCount": 0,
        "reviewCount": 0,
        "durationMs": 0,
        "ratingCounts": {key: 0 for _, key, _ in _STATS_RATINGS},
    }


def _daily_rating_summary(counts: dict[str, int]) -> dict:
    valid_count = sum(counts.values())
    return {
        "validCount": valid_count,
        "groups": [
            {
                "key": key,
                "label": label,
                "count": counts[key],
                "percentage": round(counts[key] * 100 / valid_count, 1)
                if valid_count else None,
            }
            for _, key, label in _STATS_RATINGS
        ],
    }


def _stats_timeline(events, now_dt: datetime, tz_name: str) -> list[dict]:
    """Aggregate persisted learning events across five user-calendar days."""
    today = local_date(now_dt, tz_name=tz_name)
    days = []
    actual_by_date = {}
    for offset, relative_label in _STATS_TIMELINE_DAYS:
        day = today + timedelta(days=offset)
        actual = _empty_daily_learning()
        actual_by_date[day.isoformat()] = actual
        days.append({
            "date": day.isoformat(),
            "monthDay": f"{day.month}月{day.day}日",
            "weekday": _STATS_WEEKDAYS[day.weekday()],
            "relativeLabel": relative_label,
            "isToday": offset == 0,
            "actual": actual,
        })

    rating_key_by_value = {int(rating): key for rating, key, _ in _STATS_RATINGS}
    for event in events:
        reviewed_at = parse_iso(event.get("reviewed_at"))
        if reviewed_at is None:
            continue
        actual = actual_by_date.get(local_date(reviewed_at, tz_name=tz_name).isoformat())
        if actual is None:
            continue
        actual["completedCount"] += 1
        if int(event.get("is_new") or 0):
            actual["newCount"] += 1
        else:
            actual["reviewCount"] += 1
        actual["durationMs"] += max(0, int(event.get("duration_ms") or 0))
        rating_key = rating_key_by_value.get(event.get("rating"))
        if rating_key:
            actual["ratingCounts"][rating_key] += 1

    for item in days:
        actual = item["actual"]
        counts = actual.pop("ratingCounts")
        actual["ratings"] = _daily_rating_summary(counts)
    return days


def _stats_upcoming_due(db, now_dt: datetime, tz_name: str) -> list[dict]:
    """Count cards due in the next three user-calendar days, excluding today."""
    today = local_date(now_dt, tz_name=tz_name)
    days = []
    for offset, relative_label in _STATS_UPCOMING_DAYS:
        day = today + timedelta(days=offset)
        start, end = local_date_utc_bounds(day, tz_name=tz_name)
        count = db.execute(
            """SELECT COUNT(*) n FROM sentences
               WHERE next_review_at>=? AND next_review_at<?""",
            (
                start.isoformat(timespec="seconds"),
                end.isoformat(timespec="seconds"),
            ),
        ).fetchone()["n"]
        days.append({
            "date": day.isoformat(),
            "monthDay": f"{day.month}月{day.day}日",
            "weekday": _STATS_WEEKDAYS[day.weekday()],
            "relativeLabel": relative_label,
            "count": count,
        })
    return days


def _memory_mastery_summary(sentences, now_dt: datetime) -> dict:
    """Group official per-card recall probabilities without inventing a formula."""
    counts = {key: 0 for key, _, _ in _MEMORY_MASTERY_GROUPS}
    untracked_count = 0
    for sentence in sentences:
        if not sentence.get("last_review_at") or sentence.get("stability") is None:
            untracked_count += 1
            continue
        try:
            probability = retrievability(sentence, now_dt)
        except (TypeError, ValueError, AttributeError):
            untracked_count += 1
            continue
        if not math.isfinite(probability):
            untracked_count += 1
        elif probability >= 0.95:
            counts["veryStrong"] += 1
        elif probability >= 0.90:
            counts["strong"] += 1
        elif probability >= 0.80:
            counts["atRisk"] += 1
        else:
            counts["priority"] += 1

    effective_count = sum(counts.values())
    groups = [
        {
            "key": key,
            "label": label,
            "count": counts[key],
            "percentage": round(counts[key] * 100 / effective_count, 1)
            if effective_count else None,
            "status": status,
            "includedInPercentage": True,
        }
        for key, label, status in _MEMORY_MASTERY_GROUPS
    ]
    groups.append({
        "key": "untracked",
        "label": "尚未形成有效复习记录",
        "count": untracked_count,
        "percentage": None,
        "status": "尚无有效学习记录",
        "includedInPercentage": False,
    })
    return {
        "totalSentenceCount": len(sentences),
        "effectiveSentenceCount": effective_count,
        "untrackedSentenceCount": untracked_count,
        "groups": groups,
    }


_DUE_STATUS_CONDITION = "s.next_review_at<=?"
_TODAY_STATUS_CONDITION = "re.reviewed_at>=? AND re.reviewed_at<?"


def _study_status_context(db, now_dt: datetime | None = None) -> dict[str, str]:
    """Shared clock and natural-day bounds for dashboard and detail queries."""
    now_dt = now_dt or datetime.now(timezone.utc)
    tz = user_timezone(db)
    today_start, tomorrow_start = local_day_utc_bounds(now_dt, tz_name=tz)
    return {
        "now": now_dt.isoformat(timespec="seconds"),
        "today_start": today_start.isoformat(timespec="seconds"),
        "tomorrow_start": tomorrow_start.isoformat(timespec="seconds"),
    }


def _study_status_counts(db, context: dict[str, str]):
    """Return per-collection due/today counts using the detail predicates."""
    due = {
        row["collection_id"]: row["n"]
        for row in db.execute(
            f"""SELECT s.collection_id,COUNT(*) n FROM sentences s
                WHERE {_DUE_STATUS_CONDITION} GROUP BY s.collection_id""",
            (context["now"],),
        )
    }
    today = {
        row["collection_id"]: row["n"]
        for row in db.execute(
            f"""SELECT s.collection_id,COUNT(DISTINCT re.sentence_id) n
                FROM review_events re JOIN sentences s ON s.id=re.sentence_id
                WHERE {_TODAY_STATUS_CONDITION} GROUP BY s.collection_id""",
            (context["today_start"], context["tomorrow_start"]),
        )
    }
    return due, today


def _due_sentence_count(
    db, context: dict[str, str], collection_id: int | None = None
) -> int:
    """Count due sentences with the same predicate used by dashboard status."""
    params = [context["now"]]
    where = _DUE_STATUS_CONDITION
    if collection_id is not None:
        where += " AND s.collection_id=?"
        params.append(collection_id)
    return int(db.execute(
        f"SELECT COUNT(*) n FROM sentences s WHERE {where}", params
    ).fetchone()["n"])


def _due_sentence_rows(
    db,
    context: dict[str, str],
    collection_id: int | None = None,
    limit: int | None = None,
):
    """Fetch due sentences in the canonical review-queue order."""
    params = [context["now"]]
    where = _DUE_STATUS_CONDITION
    if collection_id is not None:
        where += " AND s.collection_id=?"
        params.append(collection_id)
    query = (
        f"SELECT s.* FROM sentences s WHERE {where} "
        "ORDER BY s.next_review_at ASC,s.created_at ASC,s.id ASC"
    )
    if limit is not None:
        query += " LIMIT ?"
        params.append(limit)
    return db.execute(query, params).fetchall()


def _completed_today_count(db, now_dt: datetime, tz_name: str) -> int:
    """Count distinct sentences with a formal review event in the user's day."""
    today_start, tomorrow_start = local_day_utc_bounds(now_dt, tz_name=tz_name)
    return int(db.execute(
        """SELECT COUNT(DISTINCT sentence_id) n
           FROM review_events
           WHERE reviewed_at>=? AND reviewed_at<?""",
        (
            today_start.isoformat(timespec="seconds"),
            tomorrow_start.isoformat(timespec="seconds"),
        ),
    ).fetchone()["n"])


def _auto_review_candidate_query(
    now_dt: datetime,
    tz_name: str,
    collection_id: int | None = None,
) -> tuple[str, list]:
    """Build the shared automatic-queue predicate and parameters."""
    today_start, tomorrow_start = local_day_utc_bounds(now_dt, tz_name=tz_name)
    where = """(
        s.last_review_at IS NULL
        OR (s.last_review_at IS NOT NULL AND s.next_review_at<=?)
      )
      AND NOT EXISTS(
        SELECT 1 FROM review_events re
        WHERE re.sentence_id=s.id
          AND re.reviewed_at>=? AND re.reviewed_at<?
      )"""
    params = [
        now_dt.astimezone(timezone.utc).isoformat(timespec="seconds"),
        today_start.isoformat(timespec="seconds"),
        tomorrow_start.isoformat(timespec="seconds"),
    ]
    if collection_id is not None:
        where += " AND s.collection_id=?"
        params.append(collection_id)
    return where, params


def _auto_review_candidate_count(
    db,
    *,
    now_dt: datetime,
    tz_name: str,
    collection_id: int | None = None,
) -> int:
    where, params = _auto_review_candidate_query(now_dt, tz_name, collection_id)
    return int(db.execute(
        f"SELECT COUNT(*) n FROM sentences s WHERE {where}", params
    ).fetchone()["n"])


def _resolve_auto_review_count(requested, available: int, subject: str):
    """Strictly validate a due-session count, then clamp stale oversized input."""
    if requested in (None, "all"):
        return available, ""
    if isinstance(requested, bool):
        return None, "题目数量必须是正整数"
    if type(requested) is int:
        limit = requested
    elif isinstance(requested, str) and requested.strip().isdigit():
        limit = int(requested.strip())
    else:
        return None, "题目数量必须是正整数"
    if limit < 1:
        return None, "题目数量必须是正整数"
    notice = ""
    if limit > available:
        notice = f"{subject}只有 {available} 句，已调整为全部"
        limit = available
    return limit, notice


def _auto_review_sentence_rows(
    db,
    *,
    now_dt: datetime,
    tz_name: str,
    collection_id: int | None,
    remaining_quota: int,
    requested_count,
    subject: str = "当前可自动复习",
):
    """Build and cap the home automatic queue without changing FSRS state.

    Reviewed cards are ordered by current retrievability from the official FSRS
    package, then due/creation/id. New cards follow in creation/id order.
    """
    where, params = _auto_review_candidate_query(now_dt, tz_name, collection_id)
    candidates = db.execute(
        f"SELECT s.* FROM sentences s WHERE {where}", params
    ).fetchall()
    reviewed = [row for row in candidates if row["last_review_at"] is not None]
    new = [row for row in candidates if row["last_review_at"] is None]
    latest = datetime.max.replace(tzinfo=timezone.utc)

    def timestamp_key(value):
        return parse_iso(value) or latest

    reviewed.sort(key=lambda row: (
        retrievability(row, now_dt),
        timestamp_key(row["next_review_at"]),
        timestamp_key(row["created_at"]),
        row["id"],
    ))
    new.sort(key=lambda row: (timestamp_key(row["created_at"]), row["id"]))
    ordered = [*reviewed, *new]
    available = min(len(ordered), max(0, int(remaining_quota)))
    limit, notice = _resolve_auto_review_count(requested_count, available, subject)
    if limit is None:
        raise ValueError(notice)
    return ordered[:limit], available, notice


def _report_item_rows(db, session_id: int):
    """Return persisted report rows, including explicitly unanswered items."""
    return db.execute(
        """SELECT
             pi.session_id,pi.sentence_id,pi.position,pi.finalized_at,pi.unanswered_at,
             pi.final_status,pi.fsrs_rating,pi.easy_selected,
             CASE WHEN pi.unanswered_at IS NOT NULL
                  THEN pi.draft_answer_order_json ELSE a.answer_order_json END answer_order_json,
             COALESCE(a.sentence_snapshot_json,pi.sentence_snapshot_json) sentence_snapshot_json
           FROM practice_items pi
           LEFT JOIN attempts a ON a.id=(
             SELECT a2.id FROM attempts a2
             WHERE a2.session_id=pi.session_id AND a2.sentence_id=pi.sentence_id
             ORDER BY a2.id DESC LIMIT 1
           )
           WHERE pi.session_id=?
             AND (pi.finalized_at IS NOT NULL OR pi.unanswered_at IS NOT NULL)
           ORDER BY pi.position""",
        (session_id,),
    ).fetchall()


def _report_retry_sentence_rows(db, session_id: int, collection_id: int | None):
    """Prioritize this report's unanswered rows, then append currently due rows."""
    unanswered = db.execute(
        """SELECT s.* FROM practice_items pi
           JOIN sentences s ON s.id=pi.sentence_id
           WHERE pi.session_id=? AND pi.unanswered_at IS NOT NULL
           ORDER BY pi.position""",
        (session_id,),
    ).fetchall()
    unanswered_ids = {row["id"] for row in unanswered}
    due = _due_sentence_rows(
        db, _study_status_context(db), collection_id=collection_id
    ) if collection_id is not None else []
    return [*unanswered, *(row for row in due if row["id"] not in unanswered_ids)]


def _study_status_rows(db, collection_id: int, status: str, context: dict[str, str]):
    """Fetch one collection's status rows, already filtered and sorted by SQL."""
    if status == "due":
        return _due_sentence_rows(db, context, collection_id=collection_id)
    return db.execute(
        f"""SELECT s.*,today_events.today_last_review_at
            FROM sentences s
            JOIN (
              SELECT re.sentence_id,MAX(re.reviewed_at) today_last_review_at
              FROM review_events re
              WHERE {_TODAY_STATUS_CONDITION}
              GROUP BY re.sentence_id
            ) today_events ON today_events.sentence_id=s.id
            WHERE s.collection_id=?
            ORDER BY today_events.today_last_review_at DESC,s.id DESC""",
        (context["today_start"], context["tomorrow_start"], collection_id),
    ).fetchall()


def _server_utc_offset_label() -> str:
    """Current UTC offset of the server process, formatted as "+08:00" style.

    Python's stdlib has no portable way to read the OS's IANA zone name, only
    its current fixed offset, so this is only used for a display hint on the
    settings page (e.g. "服务器当前是 UTC+08:00"), never for calculation.
    """
    offset = datetime.now().astimezone().utcoffset()
    if offset is None:
        return ""
    total_minutes = int(offset.total_seconds() // 60)
    sign = "+" if total_minutes >= 0 else "-"
    total_minutes = abs(total_minutes)
    return f"{sign}{total_minutes // 60:02d}:{total_minutes % 60:02d}"


def _prior_consecutive_first_correct_count(
    db, session_id: int, sentence_id: int
) -> int:
    """Count consecutive first-check successes before the current session.

    Walks finalized independent practice cycles (one review_event per session)
    in stable reverse order.  A first_attempt_correct of 0 or NULL breaks the
    chain; unanswered/skipped cycles leave no review_event and neither count
    nor reset.  SQLite ``BEGIN IMMEDIATE`` serializes this read with the FSRS
    write so concurrent finalizers cannot both consume the same stale history.
    """
    rows = db.execute(
        """SELECT re.first_attempt_correct
           FROM review_events re
           JOIN practice_sessions previous_session ON previous_session.id=re.session_id
           JOIN practice_sessions current_session ON current_session.id=?
           JOIN practice_items pi
             ON pi.session_id=re.session_id AND pi.sentence_id=re.sentence_id
           WHERE re.sentence_id=?
             AND re.session_id<>?
             AND pi.finalized_at IS NOT NULL
             AND (
               previous_session.created_at<current_session.created_at
               OR (
                 previous_session.created_at=current_session.created_at
                 AND previous_session.id<current_session.id
               )
             )
           ORDER BY previous_session.created_at DESC,previous_session.id DESC,re.id DESC""",
        (session_id, sentence_id, session_id),
    ).fetchall()
    count = 0
    for row in rows:
        value = row["first_attempt_correct"]
        if value == 1:
            count += 1
            continue
        break
    return count


def _finalize_question(db, session_id: int, sentence_id: int, *, enable_fuzzing: bool):
    """Finalize exactly one practice item inside the caller's transaction."""
    item = db.execute(
        "SELECT * FROM practice_items WHERE session_id=? AND sentence_id=?",
        (session_id, sentence_id),
    ).fetchone()
    if not item:
        return None, "练习或句子不存在"
    if item["unanswered_at"]:
        return {
            "finalized": True,
            "status": "unanswered",
            "rating": None,
            "ratingLabel": None,
            "duplicate": True,
        }, None
    if item["finalized_at"]:
        rating = Rating(item["fsrs_rating"]) if item["fsrs_rating"] else None
        return {
            "finalized": True,
            "status": item["final_status"],
            "rating": RATING_NAMES.get(rating),
            "ratingLabel": RATING_LABELS_ZH.get(rating),
            "duplicate": True,
        }, None

    attempts = db.execute(
        """SELECT * FROM attempts
           WHERE session_id=? AND sentence_id=?
           ORDER BY attempt_number,id""",
        (session_id, sentence_id),
    ).fetchall()
    if not attempts:
        return None, "当前题还没有作答记录"
    facts = attempt_facts(attempts)
    prior_consecutive = (
        _prior_consecutive_first_correct_count(db, session_id, sentence_id)
        if facts.first_attempt_correct is True
        else 0
    )
    rating = determine_fsrs_rating(
        attempts,
        prior_consecutive_first_correct=prior_consecutive,
    )
    stamp = now_iso()
    final_status = "skipped" if rating is None else (
        "wrong" if rating is Rating.Again else "correct"
    )
    if rating is None:
        db.execute(
            """UPDATE practice_items
               SET finalized_at=?,final_status='skipped',fsrs_rating=NULL,easy_selected=0
               WHERE session_id=? AND sentence_id=? AND finalized_at IS NULL""",
            (stamp, session_id, sentence_id),
        )
        return {"finalized": True, "status": "skipped", "rating": None, "ratingLabel": None}, None

    row = db.execute("SELECT * FROM sentences WHERE id=?", (sentence_id,)).fetchone()
    if not row:
        return None, "句子不存在"
    duration_ms = sum(max(0, int(attempt["duration_ms"] or 0)) for attempt in attempts)
    outcome = fsrs_review(
        row,
        rating,
        duration_ms=duration_ms,
        enable_fuzzing=enable_fuzzing,
    )
    after = outcome.after
    db.execute(
        """UPDATE sentences SET fsrs_state=?,fsrs_step=?,stability=?,difficulty=?,
           last_review_at=?,next_review_at=?,fsrs_version=?,updated_at=? WHERE id=?""",
        (
            after["fsrs_state"], after["fsrs_step"], after["stability"], after["difficulty"],
            after["last_review_at"], after["next_review_at"], after["fsrs_version"], stamp,
            sentence_id,
        ),
    )
    before = outcome.before
    db.execute(
        """INSERT INTO review_events(
             sentence_id,session_id,rating,attempt_count,first_attempt_correct,
             second_attempt_correct,final_attempt_correct,rating_policy_version,
             reviewed_at,duration_ms,is_new,
             fsrs_state_before,fsrs_state_after,fsrs_step_before,fsrs_step_after,
             stability_before,stability_after,difficulty_before,difficulty_after,
             next_review_before,next_review_after,fsrs_version,created_at
           ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            sentence_id, session_id, int(rating), facts.attempt_count,
            int(facts.first_attempt_correct),
            None if facts.second_attempt_correct is None else int(facts.second_attempt_correct),
            int(facts.final_attempt_correct), RATING_POLICY_VERSION,
            after["last_review_at"], duration_ms,
            1 if before["last_review_at"] is None else 0,
            before["fsrs_state"], after["fsrs_state"], before["fsrs_step"], after["fsrs_step"],
            before["stability"], after["stability"], before["difficulty"], after["difficulty"],
            before["next_review_at"], after["next_review_at"], FSRS_VERSION, stamp,
        ),
    )
    db.execute(
        """UPDATE practice_items SET finalized_at=?,final_status=?,fsrs_rating=?,easy_selected=?
           WHERE session_id=? AND sentence_id=? AND finalized_at IS NULL""",
        (stamp, final_status, int(rating), 0, session_id, sentence_id),
    )
    return {
        "finalized": True,
        "status": final_status,
        "rating": RATING_NAMES[rating],
        "ratingLabel": RATING_LABELS_ZH[rating],
        "nextReviewAt": after["next_review_at"],
    }, None


def _persist_session_drafts(db, session_id: int, drafts) -> str | None:
    """Validate and persist temporary arrangements supplied at round submission."""
    if drafts is None:
        return None
    if not isinstance(drafts, list):
        return "临时排列格式无效"

    rows = db.execute(
        """SELECT pi.sentence_id,pi.sentence_snapshot_json,s.chunks_json,s.chinese,s.note,
                  s.japanese,s.correct_order_json,s.practice_structure_json,
                  s.chunk_source,s.chunk_schema_version,s.chunks_manually_edited,
                  s.furigana_json,s.collection_id
           FROM practice_items pi
           LEFT JOIN sentences s ON s.id=pi.sentence_id
           WHERE pi.session_id=?""",
        (session_id,),
    ).fetchall()
    by_id = {row["sentence_id"]: row for row in rows}
    updates = []
    seen = set()
    for draft in drafts:
        if not isinstance(draft, dict):
            return "临时排列格式无效"
        try:
            sentence_id = int(draft.get("sentenceId"))
        except (TypeError, ValueError):
            return "临时排列格式无效"
        answer = draft.get("answerOrder")
        if sentence_id in seen or sentence_id not in by_id or not isinstance(answer, list):
            return "临时排列格式无效"
        seen.add(sentence_id)
        row = by_id[sentence_id]
        snapshot = snapshot_dict(row["sentence_snapshot_json"])
        if not snapshot and row["chunks_json"] is not None:
            snapshot = {
                "id": sentence_id,
                "chinese": row["chinese"],
                "note": row["note"] or "",
                "japanese": row["japanese"],
                "chunks": json_load(row["chunks_json"], []),
                "correctOrder": json_load(row["correct_order_json"], []),
                "practiceStructure": json_load(row["practice_structure_json"], []),
                "chunkSource": row["chunk_source"],
                "chunkSchemaVersion": row["chunk_schema_version"],
                "chunksManuallyEdited": bool(row["chunks_manually_edited"]),
                "furigana": json_load(row["furigana_json"], []),
                "collectionId": row["collection_id"],
            }
        valid_ids = {
            chunk.get("id") for chunk in snapshot.get("chunks", [])
            if isinstance(chunk, dict) and isinstance(chunk.get("id"), str)
        }
        if any(not isinstance(value, str) or value not in valid_ids for value in answer):
            return "临时排列包含无效词块"
        if len(answer) > len(valid_ids) or len(set(answer)) != len(answer):
            return "临时排列包含无效词块"
        updates.append((json.dumps(answer), json.dumps(snapshot, ensure_ascii=False), session_id, sentence_id))

    db.executemany(
        """UPDATE practice_items
           SET draft_answer_order_json=?,sentence_snapshot_json=COALESCE(sentence_snapshot_json,?)
           WHERE session_id=? AND sentence_id=?""",
        updates,
    )
    return None


def _session_unanswered_rows(db, session_id: int):
    """Determine unanswered items from persisted valid check attempts only."""
    return db.execute(
        """SELECT pi.sentence_id,pi.position FROM practice_items pi
           WHERE pi.session_id=?
             AND pi.finalized_at IS NULL
             AND pi.unanswered_at IS NULL
             AND NOT EXISTS(
               SELECT 1 FROM attempts a
               WHERE a.session_id=pi.session_id AND a.sentence_id=pi.sentence_id
                 AND a.status IN ('correct','wrong')
             )
           ORDER BY pi.position""",
        (session_id,),
    ).fetchall()


def _session_completed_item_count(db, session_id: int) -> int:
    """Count submitted items, including any item already finalized separately."""
    return int(db.execute(
        """SELECT COUNT(*) n FROM practice_items pi
           WHERE pi.session_id=?
             AND (
               pi.finalized_at IS NOT NULL
               OR EXISTS(
                 SELECT 1 FROM attempts a
                 WHERE a.session_id=pi.session_id AND a.sentence_id=pi.sentence_id
                   AND a.status IN ('correct','wrong')
               )
             )""",
        (session_id,),
    ).fetchone()["n"])


def create_app(test_config=None):
    app = Flask(__name__, static_folder="static")
    secret = os.environ.get("APP_SECRET", "")
    app.secret_key = hashlib.sha256(secret.encode()).hexdigest() if secret else secrets.token_hex(32)
    app.config.update(
        MAX_CONTENT_LENGTH=2 * 1024 * 1024,
        SESSION_COOKIE_SECURE=truthy("SESSION_COOKIE_SECURE", True),
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        PERMANENT_SESSION_LIFETIME=timedelta(days=7),
        FSRS_ENABLE_FUZZING=truthy("FSRS_ENABLE_FUZZING", True),
    )
    if test_config:
        app.config.update(test_config)
    if app.config.get("TESTING") and not (test_config or {}).get("FSRS_ENABLE_FUZZING"):
        app.config["FSRS_ENABLE_FUZZING"] = False
    def rebuild_fonts():
        if not app.config.get("TESTING"):
            schedule_font_rebuild()
    count = int(os.environ.get("TRUST_PROXY_COUNT", "1") or 0)
    if count:
        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=count, x_proto=count, x_host=count)

    init_db(enable_fuzzing=bool(app.config["FSRS_ENABLE_FUZZING"]))
    with get_db() as db:
        username, password = os.environ.get("INIT_USERNAME", ""), os.environ.get("INIT_PASSWORD", "")
        if username and password and not auth_configured(db):
            set_setting(db, "auth_username", username.strip())
            set_setting(db, "auth_password_hash", hash_password(password))

    @app.before_request
    def protect_api():
        public = {
            "/api/health",
            "/api/auth/status",
            "/api/auth/login",
            "/api/auth/logout",
            "/api/fonts/faces.css",
            "/api/fonts/status",
        }
        if (
            not request.path.startswith("/api/")
            or request.path in public
            or request.path.startswith("/api/fonts/files/")
        ):
            return None
        with get_db() as db:
            if not auth_configured(db) or authed():
                return None
        return jsonify(error="请先登录", authRequired=True), 401

    @app.after_request
    def secure(response):
        response.headers.update({
            "X-Content-Type-Options": "nosniff",
            "X-Frame-Options": "DENY",
            "Referrer-Policy": "no-referrer",
            "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
            "Cache-Control": "no-store",
            "Content-Security-Policy": "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; font-src 'self'; connect-src 'self'; object-src 'none'; base-uri 'self'; frame-ancestors 'none'; form-action 'self'",
        })
        # Hashed active font files are content-addressed; cache forever.
        if request.path.startswith("/api/fonts/files/"):
            response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
        # App JS/CSS: allow store + revalidate so 304 works (ETag / Last-Modified).
        elif request.path.startswith("/static/"):
            response.headers["Cache-Control"] = "no-cache"
        if request.is_secure:
            response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
        return response

    @app.errorhandler(413)
    def too_large(_):
        return jsonify(error="请求内容过大"), 413

    @app.errorhandler(Exception)
    def errors(exc):
        status = getattr(exc, "code", 500)
        if int(status) >= 500:
            app.logger.exception("Unhandled error")
            return jsonify(error="服务器内部错误"), 500
        return jsonify(error=str(exc)), int(status)

    @app.get("/")
    def index():
        return send_from_directory(app.static_folder, "index.html")

    @app.get("/api/fonts/faces.css")
    def font_faces_css():
        css = faces_css_text()
        if not css:
            # Attempt a one-shot build if files are missing.
            ensure_active_fonts()
            css = faces_css_text() or "/* active fonts not ready */\n"
        return app.response_class(css, mimetype="text/css")

    @app.get("/api/fonts/files/<path:name>")
    def font_file(name):
        safe = safe_font_filename(name)
        if not safe:
            return jsonify(error="字体不存在"), 404
        return send_from_directory(active_dir(), safe, mimetype="font/woff2")

    @app.get("/api/fonts/status")
    def fonts_status():
        return jsonify(font_status())

    @app.get("/api/health")
    def health():
        return jsonify(ok=True, time=int(time.time()), tokenizer=TOKENIZER_NAME)

    @app.get("/api/auth/status")
    def auth_status():
        with get_db() as db:
            required = auth_configured(db)
        return jsonify(configured=required, authenticated=(authed() if required else True))

    @app.post("/api/auth/login")
    def login():
        body = request.get_json(silent=True) or {}
        username, password = str(body.get("username", "")).strip(), str(body.get("password", ""))
        identifiers = keys(username)
        with get_db() as db:
            remaining = lock_remaining(db, identifiers)
            if remaining:
                response = jsonify(error=f"尝试次数过多，请 {int(remaining)+1} 秒后再试")
                response.status_code = 429
                response.headers["Retry-After"] = str(int(remaining) + 1)
                return response
            good = hmac.compare_digest(username, setting(db, "auth_username")) and verify_password(password, setting(db, "auth_password_hash"))
            if not good:
                fail(db, identifiers)
                return jsonify(error="用户名或密码错误"), 401
            clear(db, identifiers)
        session.clear()
        session["authed_at"] = time.time()
        session.permanent = True
        return jsonify(ok=True)

    @app.post("/api/auth/logout")
    def logout():
        session.clear()
        return jsonify(ok=True)

    @app.get("/api/dashboard")
    def dashboard():
        with get_db() as db:
            now_dt = datetime.now(timezone.utc)
            context = _study_status_context(db, now_dt)
            tz = user_timezone(db)
            daily_limit = daily_auto_review_limit(db)
            completed_today = _completed_today_count(db, now_dt, tz)
            remaining_quota = max(0, daily_limit - completed_today)
            collections = [dict(row) for row in db.execute("""
              SELECT c.id,c.name,COUNT(s.id) total,
                SUM(CASE WHEN s.last_review_at IS NOT NULL THEN 1 ELSE 0 END) learned
              FROM collections c LEFT JOIN sentences s ON s.collection_id=c.id
              GROUP BY c.id ORDER BY c.created_at
            """)]
            due_counts, today_counts = _study_status_counts(db, context)
            for item in collections:
                item["total"], item["learned"] = int(item["total"] or 0), int(item["learned"] or 0)
                item["due"] = int(due_counts.get(item["id"], 0))
                item["today"] = int(today_counts.get(item["id"], 0))
                candidate_count = _auto_review_candidate_count(
                    db, now_dt=now_dt, tz_name=tz, collection_id=item["id"]
                )
                item["availableAutoReviewCount"] = min(candidate_count, remaining_quota)
            due_total = sum(item["due"] for item in collections)
            candidate_total = _auto_review_candidate_count(
                db, now_dt=now_dt, tz_name=tz
            )
        return jsonify(
            collections=collections,
            due=due_total,
            dailyAutoReviewLimit=daily_limit,
            completedToday=completed_today,
            remainingAutoReviewQuota=remaining_quota,
            availableAutoReviewCount=min(candidate_total, remaining_quota),
        )

    @app.get("/api/collections/<int:collection_id>/study-status/<status>")
    def collection_study_status(collection_id, status):
        if status not in {"due", "today"}:
            return jsonify(error="学习状态不存在"), 404
        with get_db() as db:
            collection = db.execute(
                "SELECT id,name FROM collections WHERE id=?", (collection_id,)
            ).fetchone()
            if not collection:
                return jsonify(error="句集不存在"), 404
            rows = _study_status_rows(db, collection_id, status, _study_status_context(db))
        sentences = [sentence_dict(row) for row in rows]
        return jsonify(
            collection=dict(collection),
            status=status,
            total=len(sentences),
            sentences=sentences,
        )

    @app.post("/api/collections")
    def create_collection():
        name = str((request.get_json(silent=True) or {}).get("name", "")).strip()
        if not name:
            return jsonify(error="句集名称不能为空"), 400
        stamp = now_iso()
        try:
            with get_db() as db:
                cursor = db.execute("INSERT INTO collections(name,created_at,updated_at) VALUES(?,?,?)", (name, stamp, stamp))
                new_id = cursor.lastrowid
            rebuild_fonts()
            return jsonify(id=new_id, name=name), 201
        except sqlite3.IntegrityError:
            return jsonify(error="句集名称已存在"), 409

    @app.patch("/api/collections/<int:collection_id>")
    def rename_collection(collection_id):
        name = str((request.get_json(silent=True) or {}).get("name", "")).strip()
        if not name:
            return jsonify(error="句集名称不能为空"), 400
        with get_db() as db:
            changed = db.execute("UPDATE collections SET name=?,updated_at=? WHERE id=?", (name, now_iso(), collection_id)).rowcount
        if changed:
            rebuild_fonts()
        return (jsonify(ok=True) if changed else (jsonify(error="句集不存在"), 404))

    @app.delete("/api/collections/<int:collection_id>")
    def delete_collection(collection_id):
        cascade = str(request.args.get("cascade", "")).lower() in ("1", "true", "yes")
        with get_db() as db:
            exists = db.execute("SELECT id FROM collections WHERE id=?", (collection_id,)).fetchone()
            if not exists:
                return jsonify(error="句集不存在"), 404
            if db.execute("SELECT COUNT(*) n FROM collections").fetchone()["n"] <= 1:
                return jsonify(error="至少保留一个句集"), 409
            ids = [row["id"] for row in db.execute("SELECT id FROM sentences WHERE collection_id=?", (collection_id,)).fetchall()]
            if ids and not cascade:
                return jsonify(error="请先移动或删除句集中的句子"), 409
            _hard_delete_sentences(db, ids)
            db.execute("DELETE FROM collections WHERE id=?", (collection_id,))
        rebuild_fonts()
        return jsonify(ok=True)

    @app.post("/api/sentences/organize")
    def organize():
        body = request.get_json(silent=True) or {}
        if not isinstance(body.get("japanese"), str) or not isinstance(body.get("chinese"), str):
            return jsonify(error="中文翻译和日语原句必须是字符串"), 400
        japanese, chinese = body["japanese"], body["chinese"].strip()
        if not japanese.strip() or not chinese:
            return jsonify(error="中文翻译和日语原句都不能为空"), 400
        analysis = analyze_sentence(japanese)
        return jsonify(
            chunks=analysis["chunks"],
            correctOrder=analysis["correctOrder"],
            practiceStructure=analysis["structure"],
            source=analysis["source"],
            schemaVersion=analysis["schemaVersion"],
            sentenceFurigana=analysis["furigana"],
        )

    def validate_sentence_payload(body):
        if not isinstance(body.get("chinese"), str) or not isinstance(body.get("japanese"), str):
            return None, "中文翻译和日语原句必须是字符串"
        note_value = body.get("note", "")
        if not isinstance(note_value, str):
            return None, "备注必须是字符串"
        note = note_value.strip()
        if len(note) > MAX_SENTENCE_NOTE_LENGTH:
            return None, f"备注不能超过 {MAX_SENTENCE_NOTE_LENGTH} 个字符"
        chinese, japanese = body["chinese"].strip(), body["japanese"]
        chunks = body.get("chunks")
        structure = body.get("practiceStructure")
        if not chinese or not japanese.strip():
            return None, "中文翻译和日语原句都不能为空"
        order = body.get("correctOrder")
        legacy_payload = structure is None
        if legacy_payload:
            try:
                chunks, structure = structure_from_manual_chunks(japanese, chunks)
                order = [item["id"] for item in chunks]
            except (TypeError, ValueError) as exc:
                return None, str(exc)
        valid, message = validate_practice_data(japanese, chunks, structure, order)
        if not valid:
            return None, message
        chunks = [
            {
                "id": item["id"], "text": item["text"],
                "start": item["start"], "end": item["end"],
            }
            for item in chunks
        ]
        structure = [dict(element) for element in structure]
        order = order or [item["id"] for item in chunks]
        ids = [item["id"] for item in chunks]
        if order != ids:
            return None, "正确词块顺序必须与原句中的词块顺序一致"
        source = "manual" if legacy_payload else body.get("chunkSource")
        if source not in {"ginza", "fallback", "manual", "manual_migrated"}:
            source = "manual" if body.get("chunksManuallyEdited") is True else "ginza"
        manually_edited = legacy_payload or body.get("chunksManuallyEdited") is True or source.startswith("manual")
        try:
            collection_id = int(body.get("collectionId"))
        except (ValueError, TypeError):
            return None, "请选择所属句集"
        return {
            "collection_id": collection_id, "chinese": chinese, "note": note,
            "japanese": japanese,
            "chunks": chunks, "order": order, "structure": structure,
            "source": source, "manually_edited": manually_edited,
        }, ""

    @app.post("/api/sentences")
    def create_sentence():
        item, error = validate_sentence_payload(request.get_json(silent=True) or {})
        if error:
            return jsonify(error=error), 400
        stamp = now_iso()
        try:
            furigana_json = json.dumps(furigana_segments(item["japanese"]), ensure_ascii=False)
            with get_db() as db:
                card = card_fields(new_card(0, datetime.now(timezone.utc)))
                cursor = db.execute("""INSERT INTO sentences(
                    collection_id,chinese,note,japanese,chunks_json,correct_order_json,
                    practice_structure_json,chunk_source,chunk_schema_version,
                    chunks_manually_edited,furigana_json,
                    fsrs_state,fsrs_step,stability,difficulty,last_review_at,next_review_at,
                    fsrs_version,created_at,updated_at
                  ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", (
                    item["collection_id"], item["chinese"], item["note"], item["japanese"],
                    json.dumps(item["chunks"], ensure_ascii=False), json.dumps(item["order"]),
                    json.dumps(item["structure"], ensure_ascii=False), item["source"],
                    CHUNK_SCHEMA_VERSION, int(item["manually_edited"]),
                    furigana_json, card["fsrs_state"], card["fsrs_step"], card["stability"],
                    card["difficulty"], card["last_review_at"], card["next_review_at"],
                    FSRS_VERSION, stamp, stamp,
                ))
                row = db.execute("SELECT * FROM sentences WHERE id=?", (cursor.lastrowid,)).fetchone()
            rebuild_fonts()
            return jsonify(sentence=sentence_dict(row)), 201
        except sqlite3.IntegrityError:
            return jsonify(error="所属句集不存在"), 400

    @app.get("/api/sentences")
    def list_sentences():
        terms, params = ["1=1"], []
        if request.args.get("collectionId"):
            terms.append("s.collection_id=?"); params.append(request.args["collectionId"])
        search = request.args.get("search", "").strip()
        if search:
            terms.append("(s.chinese LIKE ? OR s.japanese LIKE ?)"); params.extend([f"%{search}%", f"%{search}%"])
        sort = {
            "created": "s.created_at DESC",
            "recent": "COALESCE(s.last_review_at,'') DESC",
            "error": "COALESCE(s.difficulty,0) DESC",
        }.get(request.args.get("sort"), "s.created_at DESC")
        with get_db() as db:
            rows = db.execute(f"SELECT s.*,c.name collection_name FROM sentences s JOIN collections c ON c.id=s.collection_id WHERE {' AND '.join(terms)} ORDER BY {sort}", params).fetchall()
        return jsonify(sentences=[sentence_dict(row) for row in rows])

    @app.get("/api/sentences/<int:sentence_id>")
    def get_sentence(sentence_id):
        with get_db() as db:
            row = db.execute("SELECT s.*,c.name collection_name FROM sentences s JOIN collections c ON c.id=s.collection_id WHERE s.id=?", (sentence_id,)).fetchone()
        return jsonify(sentence=sentence_dict(row)) if row else (jsonify(error="句子不存在"), 404)

    @app.put("/api/sentences/<int:sentence_id>")
    def update_sentence(sentence_id):
        item, error = validate_sentence_payload(request.get_json(silent=True) or {})
        if error:
            return jsonify(error=error), 400
        furigana_json = json.dumps(furigana_segments(item["japanese"]), ensure_ascii=False)
        with get_db() as db:
            changed = db.execute(
                """UPDATE sentences SET collection_id=?,chinese=?,note=?,japanese=?,
                   chunks_json=?,correct_order_json=?,practice_structure_json=?,
                   chunk_source=?,chunk_schema_version=?,chunks_manually_edited=?,
                   furigana_json=?,updated_at=? WHERE id=?""",
                (
                    item["collection_id"], item["chinese"], item["note"], item["japanese"],
                    json.dumps(item["chunks"], ensure_ascii=False), json.dumps(item["order"]),
                    json.dumps(item["structure"], ensure_ascii=False), item["source"],
                    CHUNK_SCHEMA_VERSION, int(item["manually_edited"]), furigana_json,
                    now_iso(), sentence_id,
                ),
            ).rowcount
        if changed:
            rebuild_fonts()
        return jsonify(ok=True) if changed else (jsonify(error="句子不存在"), 404)

    @app.post("/api/sentences/batch-note")
    def batch_note_sentences():
        body = request.get_json(silent=True)
        if not isinstance(body, dict):
            return jsonify(error="请求内容必须是 JSON 对象"), 400

        raw_ids = body.get("sentenceIds")
        if not isinstance(raw_ids, list) or not raw_ids:
            return jsonify(error="sentenceIds 必须是非空正整数数组"), 400
        if any(type(value) is not int or value <= 0 for value in raw_ids):
            return jsonify(error="sentenceIds 必须是非空正整数数组"), 400
        sentence_ids = list(dict.fromkeys(raw_ids))

        note_value = body.get("note")
        if not isinstance(note_value, str):
            return jsonify(error="备注必须是字符串"), 400
        note = note_value.strip()
        if not note:
            return jsonify(error="备注内容不能为空"), 400
        if len(note) > MAX_SENTENCE_NOTE_LENGTH:
            return jsonify(error=f"备注不能超过 {MAX_SENTENCE_NOTE_LENGTH} 个字符"), 400

        placeholders = ",".join("?" for _ in sentence_ids)
        changed_ids = []
        db = get_db()
        try:
            with db:
                db.execute("BEGIN IMMEDIATE")
                rows = db.execute(
                    f"SELECT id,note FROM sentences WHERE id IN ({placeholders})",
                    sentence_ids,
                ).fetchall()
                rows_by_id = {row["id"]: row for row in rows}
                missing_ids = [
                    sentence_id for sentence_id in sentence_ids
                    if sentence_id not in rows_by_id
                ]
                if missing_ids:
                    missing_text = "、".join(str(sentence_id) for sentence_id in missing_ids)
                    return jsonify(error=f"句子不存在：{missing_text}；整批未作修改"), 404

                for sentence_id in sentence_ids:
                    existing = rows_by_id[sentence_id]["note"] or ""
                    if existing != note:
                        changed_ids.append(sentence_id)

                if changed_ids:
                    stamp = now_iso()
                    for sentence_id in changed_ids:
                        db.execute(
                            "UPDATE sentences SET note=?,updated_at=? WHERE id=?",
                            (note, stamp, sentence_id),
                        )
        finally:
            db.close()

        if changed_ids:
            rebuild_fonts()
        return jsonify(ok=True, updated=len(changed_ids))

    @app.post("/api/sentences/move")
    def move_sentences():
        body = request.get_json(silent=True) or {}
        raw_ids = body.get("sentenceIds")
        if not isinstance(raw_ids, list) or not raw_ids:
            return jsonify(error="请选择要转移的句子"), 400
        try:
            sentence_ids = [int(value) for value in raw_ids]
            target_id = int(body.get("targetCollectionId"))
        except (ValueError, TypeError):
            return jsonify(error="参数无效"), 400
        with get_db() as db:
            if not db.execute("SELECT id FROM collections WHERE id=?", (target_id,)).fetchone():
                return jsonify(error="目标句集不存在"), 404
            placeholders = ",".join("?" for _ in sentence_ids)
            moved = db.execute(
                f"UPDATE sentences SET collection_id=?,updated_at=? WHERE id IN ({placeholders})",
                [target_id, now_iso(), *sentence_ids],
            ).rowcount
        if moved:
            rebuild_fonts()
        return jsonify(ok=True, moved=moved)

    @app.post("/api/sentences/rechunk")
    def rechunk_sentences():
        body = request.get_json(silent=True) or {}
        if not isinstance(body, dict):
            return jsonify(error="请求内容必须是 JSON 对象"), 400
        raw_ids = body.get("sentenceIds")
        if not isinstance(raw_ids, list) or not raw_ids:
            return jsonify(error="请选择要重新分块的句子"), 400
        if any(type(value) is not int or value <= 0 for value in raw_ids):
            return jsonify(error="sentenceIds 必须是非空正整数数组"), 400
        sentence_ids = list(dict.fromkeys(raw_ids))
        placeholders = ",".join("?" for _ in sentence_ids)

        db = get_db()
        try:
            rows = db.execute(
                f"SELECT id,japanese FROM sentences WHERE id IN ({placeholders})",
                sentence_ids,
            ).fetchall()
        finally:
            db.close()
        rows_by_id = {row["id"]: row for row in rows}
        missing_ids = [sentence_id for sentence_id in sentence_ids if sentence_id not in rows_by_id]
        if missing_ids:
            missing_text = "、".join(str(sentence_id) for sentence_id in missing_ids)
            return jsonify(error=f"句子不存在：{missing_text}；整批未作修改"), 404

        prepared = []
        for sentence_id in sentence_ids:
            japanese = rows_by_id[sentence_id]["japanese"]
            try:
                analysis = analyze_sentence(japanese)
                chunks = analysis["chunks"]
                order = analysis["correctOrder"]
                structure = analysis["structure"]
                valid, message = validate_practice_data(japanese, chunks, structure, order)
                if not valid:
                    raise ValueError(message)
                source = analysis["source"]
                schema_version = analysis["schemaVersion"]
                furigana = analysis["furigana"]
                if source not in {"ginza", "fallback"}:
                    raise ValueError("自动分块来源无效")
                if type(schema_version) is not int or schema_version <= 0:
                    raise ValueError("分块结构版本无效")
                prepared.append((
                    json.dumps(chunks, ensure_ascii=False),
                    json.dumps(order, ensure_ascii=False),
                    json.dumps(structure, ensure_ascii=False),
                    source,
                    schema_version,
                    json.dumps(furigana, ensure_ascii=False),
                    sentence_id,
                    japanese,
                ))
            except Exception as exc:
                return jsonify(
                    error=f"句子 {sentence_id} 重新分块失败：{exc}；整批未作修改"
                ), 422

        stamp = now_iso()
        db = get_db()
        try:
            with db:
                for (
                    chunks_json, order_json, structure_json, source,
                    schema_version, furigana_json, sentence_id, japanese,
                ) in prepared:
                    changed = db.execute(
                        """UPDATE sentences SET
                           chunks_json=?,correct_order_json=?,practice_structure_json=?,
                           chunk_source=?,chunk_schema_version=?,chunks_manually_edited=0,
                           furigana_json=?,updated_at=?
                           WHERE id=? AND japanese=?""",
                        (
                            chunks_json, order_json, structure_json, source,
                            schema_version, furigana_json, stamp, sentence_id, japanese,
                        ),
                    ).rowcount
                    if changed != 1:
                        raise RuntimeError(f"句子 {sentence_id} 在处理期间发生变化")
        except Exception as exc:
            return jsonify(error=f"批量重新分块失败：{exc}；整批已回滚"), 409
        finally:
            db.close()

        rebuild_fonts()
        return jsonify(ok=True, updated=len(prepared))

    @app.delete("/api/sentences/<int:sentence_id>")
    def delete_sentence(sentence_id):
        # Hard-delete related stats/history first (before FK SET NULL would orphan them).
        with get_db() as db:
            exists = db.execute("SELECT id FROM sentences WHERE id=?", (sentence_id,)).fetchone()
            if not exists:
                return jsonify(error="句子不存在"), 404
            _hard_delete_sentences(db, [sentence_id])
        rebuild_fonts()
        return jsonify(ok=True)

    @app.post("/api/practice/sessions")
    def start_session():
        raw_body = request.get_json(silent=True)
        if raw_body is not None and not isinstance(raw_body, dict):
            return jsonify(error="请求内容必须是 JSON 对象"), 400
        body = raw_body or {}
        ids = body.get("sentenceIds")
        notice = ""
        with get_db() as db:
            if isinstance(ids, list) and ids:
                try:
                    clean = [int(value) for value in ids]
                except (ValueError, TypeError):
                    return jsonify(error="参数无效"), 400
                placeholders = ",".join("?" for _ in clean)
                rows = db.execute(f"SELECT id FROM sentences WHERE id IN ({placeholders})", clean).fetchall()
                selected = [row["id"] for row in rows]
                source = "selected"
            elif body.get("scope") == "report_retry":
                try:
                    report_id = int(body.get("reportId"))
                except (TypeError, ValueError):
                    return jsonify(error="报告参数无效"), 400
                report_row = db.execute(
                    """SELECT id FROM practice_sessions
                       WHERE id=? AND completed_at IS NOT NULL AND report_deleted_at IS NULL""",
                    (report_id,),
                ).fetchone()
                if not report_row:
                    return jsonify(error="报告不存在"), 404
                report_items = _report_item_rows(db, report_id)
                collection = _report_collection(db, report_items)
                candidates = _report_retry_sentence_rows(
                    db, report_id, collection["id"] if collection else None
                )
                available = len(candidates)
                if not available:
                    return jsonify(error="当前没有可再次练习的句子"), 400
                limit, msg = _resolve_limit(body.get("count"), available, "当前可再次练习")
                if limit is None:
                    return jsonify(error=msg), 400
                notice = msg
                selected = [row["id"] for row in candidates[:limit]]
                source = "report_retry"
            elif body.get("scope") == "collection" and body.get("collectionId"):
                collection_id = int(body["collectionId"])
                available = db.execute("SELECT COUNT(*) n FROM sentences WHERE collection_id=?", (collection_id,)).fetchone()["n"]
                if not available:
                    return jsonify(error="当前句集还没有句子"), 400
                limit, msg = _resolve_limit(body.get("count"), available, "当前句集")
                if limit is None:
                    return jsonify(error=msg), 400
                notice = msg
                selected = [row["id"] for row in db.execute("SELECT id FROM sentences WHERE collection_id=? ORDER BY RANDOM() LIMIT ?", (collection_id, limit))]
                source = "collection"
            else:
                collection_id = None
                if body.get("collectionId") not in (None, ""):
                    try:
                        collection_id = int(body["collectionId"])
                    except (TypeError, ValueError):
                        return jsonify(error="参数无效"), 400
                db.execute("BEGIN IMMEDIATE")
                now_dt = datetime.now(timezone.utc)
                tz = user_timezone(db)
                daily_limit = daily_auto_review_limit(db)
                completed_today = _completed_today_count(db, now_dt, tz)
                remaining_quota = max(0, daily_limit - completed_today)
                if remaining_quota == 0:
                    return jsonify(
                        error="今日自动复习计划已完成。仍可进入句集进行专项练习，或在练习报告中再练一轮。",
                        remainingAutoReviewQuota=0,
                    ), 409
                subject = (
                    "当前句集可自动复习"
                    if collection_id is not None
                    else "当前可自动复习"
                )
                try:
                    rows, available, notice = _auto_review_sentence_rows(
                        db,
                        now_dt=now_dt,
                        tz_name=tz,
                        collection_id=collection_id,
                        remaining_quota=remaining_quota,
                        requested_count=body.get("count"),
                        subject=subject,
                    )
                except ValueError as exc:
                    return jsonify(error=str(exc)), 400
                if not available:
                    return jsonify(error="当前没有可自动安排的待复习句子"), 400
                selected = [row["id"] for row in rows]
                source = "due"
            if not selected:
                return jsonify(error="当前没有待复习句子"), 400
            cursor = db.execute("INSERT INTO practice_sessions(source,sentence_ids_json,total,created_at) VALUES(?,?,?,?)", (source, json.dumps(selected), len(selected), now_iso()))
            session_id = cursor.lastrowid
            rows = db.execute(f"SELECT * FROM sentences WHERE id IN ({','.join('?' for _ in selected)})", selected).fetchall()
            rows_by_id = {row["id"]: row for row in rows}
            db.executemany(
                """INSERT INTO practice_items(
                     session_id,sentence_id,position,sentence_snapshot_json
                   ) VALUES(?,?,?,?)""",
                [
                    (
                        session_id, sentence_id, position,
                        json.dumps(sentence_snapshot(rows_by_id[sentence_id]), ensure_ascii=False),
                    )
                    for position, sentence_id in enumerate(selected)
                ],
            )
            mapped = {row["id"]: sentence_dict(row) for row in rows}
        return jsonify(sessionId=session_id, sentences=[mapped[x] for x in selected], notice=notice), 201

    @app.post("/api/practice/sessions/<int:session_id>/attempts")
    def record_attempt(session_id):
        body = request.get_json(silent=True) or {}
        try:
            sentence_id = int(body.get("sentenceId", 0))
        except (ValueError, TypeError):
            return jsonify(error="参数无效"), 400
        action = str(body.get("action", "check"))
        if action not in {"check", "skip"}:
            return jsonify(error="核对动作无效"), 400
        client_attempt_id = body.get("attemptId")
        if (
            not isinstance(client_attempt_id, str)
            or not client_attempt_id.strip()
            or len(client_attempt_id) > 128
        ):
            return jsonify(error="缺少有效的 attemptId"), 400
        client_attempt_id = client_attempt_id.strip()
        answer = body.get("answerOrder")
        if not isinstance(answer, list):
            answer = []
        try:
            duration_ms = max(0, int(body.get("durationMs") or 0))
        except (TypeError, ValueError):
            duration_ms = 0
        stamp = now_iso()
        with get_db() as db:
            db.execute("BEGIN IMMEDIATE")
            duplicate = db.execute(
                "SELECT * FROM attempts WHERE client_attempt_id=?",
                (client_attempt_id,),
            ).fetchone()
            if duplicate:
                if (
                    duplicate["session_id"] != session_id
                    or duplicate["sentence_id"] != sentence_id
                ):
                    return jsonify(error="attemptId 已用于其他核对请求"), 409
                snapshot = snapshot_dict(duplicate["sentence_snapshot_json"])
                status = duplicate["status"]
                return jsonify(
                    attemptId=duplicate["id"],
                    clientAttemptId=duplicate["client_attempt_id"],
                    attemptNumber=duplicate["attempt_number"],
                    status=status,
                    correctOrder=snapshot.get("correctOrder", []),
                    correct=status == "correct",
                    duplicate=True,
                )
            practice = db.execute("SELECT * FROM practice_sessions WHERE id=?", (session_id,)).fetchone()
            row = db.execute("SELECT * FROM sentences WHERE id=?", (sentence_id,)).fetchone()
            item_row = db.execute(
                """SELECT finalized_at,unanswered_at,sentence_snapshot_json
                   FROM practice_items
                   WHERE session_id=? AND sentence_id=?""",
                (session_id, sentence_id),
            ).fetchone()
            if not practice or not row or not item_row:
                return jsonify(error="练习或句子不存在"), 404
            if practice["completed_at"]:
                return jsonify(error="本轮练习已经提交"), 409
            if item_row["finalized_at"] or item_row["unanswered_at"]:
                return jsonify(error="当前题已经结束"), 409
            snapshot = snapshot_dict(item_row["sentence_snapshot_json"])
            item = snapshot if snapshot.get("chunks") and snapshot.get("correctOrder") else sentence_snapshot(row)
            status = "skipped" if action == "skip" else ("correct" if answers_match(answer, item["correctOrder"], item["chunks"]) else "wrong")
            attempt_number = db.execute(
                """SELECT COALESCE(MAX(attempt_number),0)+1 next_number
                   FROM attempts WHERE session_id=? AND sentence_id=?""",
                (session_id, sentence_id),
            ).fetchone()["next_number"]
            cursor = db.execute(
                """INSERT INTO attempts(
                     session_id,sentence_id,client_attempt_id,attempt_number,status,
                     answer_order_json,sentence_snapshot_json,duration_ms,created_at
                   ) VALUES(?,?,?,?,?,?,?,?,?)""",
                (
                    session_id, sentence_id, client_attempt_id, attempt_number,
                    status, json.dumps(answer),
                    json.dumps(item, ensure_ascii=False), duration_ms, stamp,
                ),
            )
        return jsonify(
            attemptId=cursor.lastrowid,
            clientAttemptId=client_attempt_id,
            attemptNumber=attempt_number,
            status=status,
            correctOrder=item["correctOrder"],
            correct=status == "correct",
        )

    @app.post("/api/practice/sessions/<int:session_id>/sentences/<int:sentence_id>/complete")
    def complete_question(session_id, sentence_id):
        body = request.get_json(silent=True) or {}
        with get_db() as db:
            db.execute("BEGIN IMMEDIATE")
            result, error = _finalize_question(
                db, session_id, sentence_id,
                enable_fuzzing=bool(app.config["FSRS_ENABLE_FUZZING"]),
            )
            if error:
                return jsonify(error=error), 404 if "不存在" in error else 409
        return jsonify(result)

    @app.post("/api/practice/sessions/<int:session_id>/complete")
    def complete_session(session_id):
        body = request.get_json(silent=True) or {}
        completion_mode = body.get("completionMode", "normal")
        if completion_mode not in {"normal", "early_exit"}:
            return jsonify(error="练习结束模式无效"), 400
        confirm_unanswered = (
            body.get("confirmUnanswered") is True or completion_mode == "early_exit"
        )
        with get_db() as db:
            db.execute("BEGIN IMMEDIATE")
            practice = db.execute("SELECT * FROM practice_sessions WHERE id=?", (session_id,)).fetchone()
            if not practice:
                return jsonify(error="练习不存在"), 404
            if practice["completed_at"]:
                return jsonify(
                    ok=True,
                    reportId=session_id,
                    duplicate=True,
                    **_session_completion_metadata(practice),
                )
            completed_before_submit = _session_completed_item_count(db, session_id)
            if completion_mode == "early_exit" and completed_before_submit == 0:
                return jsonify(
                    error="当前还没有完成任何题目",
                    noCompletedItems=True,
                    completedCount=0,
                    unansweredCount=int(practice["total"] or 0),
                ), 409
            draft_error = _persist_session_drafts(
                db, session_id, body.get("draftAnswers")
            )
            if draft_error:
                return jsonify(error=draft_error), 400
            unanswered_rows = _session_unanswered_rows(db, session_id)
            unanswered_count = len(unanswered_rows)
            if unanswered_count and not confirm_unanswered:
                return jsonify(
                    error=f"本轮还有 {unanswered_count} 题未回答",
                    unansweredCount=unanswered_count,
                    requiresConfirmation=True,
                ), 409
            pending = db.execute(
                """SELECT pi.sentence_id FROM practice_items pi
                   WHERE pi.session_id=? AND pi.finalized_at IS NULL
                     AND pi.unanswered_at IS NULL
                     AND EXISTS(
                       SELECT 1 FROM attempts a
                       WHERE a.session_id=pi.session_id AND a.sentence_id=pi.sentence_id
                         AND a.status IN ('correct','wrong')
                     )""",
                (session_id,),
            ).fetchall()
            for pending_item in pending:
                _, error = _finalize_question(
                    db, session_id, pending_item["sentence_id"],
                    enable_fuzzing=bool(app.config["FSRS_ENABLE_FUZZING"]),
                )
                if error:
                    raise RuntimeError(error)
            stamp = now_iso()
            if unanswered_rows:
                db.executemany(
                    """UPDATE practice_items SET unanswered_at=?
                       WHERE session_id=? AND sentence_id=?
                         AND finalized_at IS NULL AND unanswered_at IS NULL""",
                    [
                        (stamp, session_id, row["sentence_id"])
                        for row in unanswered_rows
                    ],
                )
            counts = {
                row["final_status"]: row["n"]
                for row in db.execute(
                    """SELECT final_status,COUNT(*) n FROM practice_items
                       WHERE session_id=? AND finalized_at IS NOT NULL GROUP BY final_status""",
                    (session_id,),
                )
            }
            db.execute(
                """UPDATE practice_sessions SET correct=?,wrong=?,skipped=?,unanswered=?,
                   completion_mode=?,completed_at=COALESCE(completed_at,?) WHERE id=?""",
                (
                    counts.get("correct", 0), counts.get("wrong", 0),
                    counts.get("skipped", 0), unanswered_count, completion_mode,
                    stamp, session_id,
                ),
            )
            completed_practice = db.execute(
                "SELECT * FROM practice_sessions WHERE id=?", (session_id,)
            ).fetchone()
        return jsonify(
            ok=True,
            reportId=session_id,
            **_session_completion_metadata(completed_practice),
        )

    @app.get("/api/reports")
    def reports():
        with get_db() as db:
            rows = [dict(row) for row in db.execute("""SELECT * FROM practice_sessions
                WHERE completed_at IS NOT NULL AND report_deleted_at IS NULL
                ORDER BY created_at DESC LIMIT 100""")]
            rating_rows = db.execute(
                """SELECT pi.session_id,pi.fsrs_rating,pi.final_status
                   FROM practice_items pi
                   JOIN practice_sessions ps ON ps.id=pi.session_id
                   WHERE ps.completed_at IS NOT NULL
                     AND ps.report_deleted_at IS NULL
                     AND pi.finalized_at IS NOT NULL"""
            ).fetchall()
        counts_by_session = {}
        for rating_row in rating_rows:
            counts_by_session.setdefault(rating_row["session_id"], []).append(rating_row)
        for row in rows:
            row["ratingCounts"] = _rating_counts(counts_by_session.get(row["id"], []))
            row.update(_session_completion_metadata(row))
        return jsonify(reports=rows)

    @app.get("/api/reports/<int:session_id>")
    def report(session_id):
        with get_db() as db:
            practice = db.execute("SELECT * FROM practice_sessions WHERE id=?", (session_id,)).fetchone()
            if not practice or not practice["completed_at"] or practice["report_deleted_at"]:
                return jsonify(error="报告不存在"), 404
            items_rows = _report_item_rows(db, session_id)
            collection = _report_collection(db, items_rows)
            retry_rows = _report_retry_sentence_rows(
                db, session_id, collection["id"] if collection else None
            ) if practice["completed_at"] else []
            retry_unanswered = sum(
                1 for row in items_rows
                if row["unanswered_at"] is not None
                and any(candidate["id"] == row["sentence_id"] for candidate in retry_rows)
            )
        items = []
        for attempt in items_rows:
            snap = snapshot_dict(attempt["sentence_snapshot_json"])
            by_id = {chunk["id"]: chunk for chunk in snap.get("chunks", [])}
            answer = json_load(attempt["answer_order_json"], [])
            rating = Rating(attempt["fsrs_rating"]) if attempt["fsrs_rating"] else None
            status = "unanswered" if attempt["unanswered_at"] else attempt["final_status"]
            items.append({
                "status": status, "answerOrder": answer,
                "answerText": "".join(by_id.get(value, {}).get("text", "") for value in answer),
                "rating": RATING_NAMES.get(rating), "ratingLabel": RATING_LABELS_ZH.get(rating), **snap,
            })
        payload = dict(practice)
        payload["ratingCounts"] = _rating_counts(items_rows)
        payload.update(_session_completion_metadata(practice))
        payload["collection"] = collection
        payload["retry"] = {
            "availableCount": len(retry_rows),
            "unansweredCount": retry_unanswered,
        }
        payload["items"] = items
        return jsonify(report=payload)

    @app.delete("/api/reports/<int:session_id>")
    def delete_report(session_id):
        with get_db() as db:
            exists = db.execute("SELECT id FROM practice_sessions WHERE id=? AND report_deleted_at IS NULL", (session_id,)).fetchone()
            if not exists:
                return jsonify(error="报告不存在"), 404
            db.execute("UPDATE practice_sessions SET report_deleted_at=? WHERE id=?", (now_iso(), session_id))
        return jsonify(ok=True)

    @app.get("/api/settings/auth")
    def get_auth_settings():
        with get_db() as db:
            return jsonify(configured=auth_configured(db), username=setting(db, "auth_username"))

    @app.put("/api/settings/auth")
    def save_auth_settings():
        body = request.get_json(silent=True) or {}
        clear_auth = bool(body.get("clearAuth"))
        username = str(body.get("username", "")).strip()
        password = body.get("password", "")
        if not isinstance(password, str):
            password = ""
        with get_db() as db:
            currently_configured = auth_configured(db)
            if clear_auth:
                db.execute("DELETE FROM settings WHERE key IN ('auth_username','auth_password_hash')")
                session.clear()
                return jsonify(ok=True, configured=False)
            if not username:
                return jsonify(error="用户名不能为空"), 400
            if not password and not currently_configured:
                return jsonify(error="首次启用认证时必须设置密码"), 400
            set_setting(db, "auth_username", username)
            if password:
                set_setting(db, "auth_password_hash", hash_password(password))
        session["authed_at"] = time.time()
        session["username"] = username
        session.permanent = True
        return jsonify(ok=True, configured=True, username=username)

    @app.get("/api/settings/fsrs")
    def get_fsrs_settings():
        return jsonify(
            system="FSRS",
            desiredRetention=DESIRED_RETENTION,
            maximumIntervalDays=MAXIMUM_INTERVAL_DAYS,
            version=FSRS_VERSION,
        )

    @app.get("/api/settings/daily-plan")
    def get_daily_plan_settings():
        with get_db() as db:
            value = daily_auto_review_limit(db)
        return jsonify(dailyAutoReviewLimit=value)

    @app.put("/api/settings/daily-plan")
    def save_daily_plan_settings():
        body = request.get_json(silent=True)
        if not isinstance(body, dict):
            return jsonify(error="请求内容必须是 JSON 对象"), 400
        value = body.get("dailyAutoReviewLimit")
        if (
            type(value) is not int
            or not MIN_DAILY_AUTO_REVIEW_LIMIT <= value <= MAX_DAILY_AUTO_REVIEW_LIMIT
        ):
            return jsonify(
                error=(
                    "每日自动复习上限必须是 "
                    f"{MIN_DAILY_AUTO_REVIEW_LIMIT} 到 "
                    f"{MAX_DAILY_AUTO_REVIEW_LIMIT} 之间的整数"
                )
            ), 400
        with get_db() as db:
            set_setting(db, DAILY_AUTO_REVIEW_LIMIT_KEY, str(value))
        return jsonify(ok=True, dailyAutoReviewLimit=value)

    @app.get("/api/settings/timezone")
    def get_timezone_settings():
        with get_db() as db:
            tz = user_timezone(db)
        return jsonify(timezone=tz, serverUtcOffset=_server_utc_offset_label())

    @app.put("/api/settings/timezone")
    def save_timezone_settings():
        body = request.get_json(silent=True) or {}
        tz = str(body.get("timezone", "")).strip()
        if tz and not is_valid_timezone(tz):
            return jsonify(error="无效的时区名称"), 400
        with get_db() as db:
            set_setting(db, "user_timezone", tz)
        return jsonify(ok=True, timezone=tz)

    # ---- Learning overview statistics ----

    @app.get("/api/stats/summary")
    def stats_summary():
        now_dt = datetime.now(timezone.utc)
        now = now_dt.isoformat(timespec="seconds")
        with get_db() as db:
            tz = user_timezone(db)
            today = local_date(now_dt, tz_name=tz)
            event_start = local_date_utc_bounds(
                today - timedelta(days=4), tz_name=tz
            )[0].isoformat(timespec="seconds")
            event_end = local_date_utc_bounds(
                today + timedelta(days=1), tz_name=tz
            )[0].isoformat(timespec="seconds")
            events = [dict(row) for row in db.execute(
                """SELECT rating,reviewed_at,duration_ms,is_new FROM review_events
                   WHERE reviewed_at>=? AND reviewed_at<? ORDER BY reviewed_at,id""",
                (event_start, event_end),
            )]
            sentences = [dict(row) for row in db.execute("SELECT * FROM sentences")]
            timeline = _stats_timeline(events, now_dt, tz)
            upcoming_due = _stats_upcoming_due(db, now_dt, tz)

        return jsonify(
            generatedAt=now,
            timezone={
                "name": tz or None,
                "source": "user" if tz else "server",
            },
            timeline=timeline,
            upcomingDue=upcoming_due,
            memoryMastery=_memory_mastery_summary(sentences, now_dt),
        )

    # Build content-subset fonts at startup (no-op if sources missing / already current).
    if not app.config.get("TESTING"):
        try:
            ensure_active_fonts()
        except Exception:
            app.logger.exception("Initial active font build failed")

    return app


app = create_app()
