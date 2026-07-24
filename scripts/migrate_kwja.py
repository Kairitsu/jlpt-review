#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import logging
import sys
from collections import Counter
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import db as database
from card_service import (
    corpus_readings,
    ensure_sentence_order_card,
    reconcile_reading_cards,
)
from kwja_analyzer import input_sha256
from reading_cards import generate_reading_cards
from tokenizer import analyze_sentence, validate_practice_data


LOGGER = logging.getLogger("kwja_migration")


def configure_database(path: Path) -> None:
    resolved = path.resolve()
    database.DATA_DIR = resolved.parent
    database.DB_PATH = resolved


def seed_queue(*, force_all: bool) -> int:
    stamp = database.now_iso()
    with database.get_db() as conn:
        conn.execute("BEGIN IMMEDIATE")
        rows = conn.execute("SELECT * FROM sentences ORDER BY id").fetchall()
        for row in rows:
            sha = input_sha256(row["japanese"])
            existing = conn.execute(
                "SELECT * FROM kwja_migration_items WHERE sentence_id=?",
                (row["id"],),
            ).fetchone()
            if existing and existing["input_sha256"] == sha and not force_all:
                continue
            conn.execute(
                """INSERT INTO kwja_migration_items(
                     sentence_id,status,input_sha256,old_analysis_source,
                     old_analysis_version,old_chunks_json,
                     old_correct_order_json,old_practice_structure_json,
                     old_furigana_json,old_chunks_manually_edited,
                     error_message,started_at,completed_at,updated_at
                   ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(sentence_id) DO UPDATE SET
                     status='pending',
                     input_sha256=excluded.input_sha256,
                     old_analysis_source=excluded.old_analysis_source,
                     old_analysis_version=excluded.old_analysis_version,
                     old_chunks_json=excluded.old_chunks_json,
                     old_correct_order_json=excluded.old_correct_order_json,
                     old_practice_structure_json=excluded.old_practice_structure_json,
                     old_furigana_json=excluded.old_furigana_json,
                     old_chunks_manually_edited=excluded.old_chunks_manually_edited,
                     new_analysis_source=NULL,new_analysis_version=NULL,
                     phrase_count=NULL,reading_card_count=NULL,
                     reading_skip_count=NULL,reading_skip_reasons_json='{}',
                     error_message=NULL,started_at=NULL,completed_at=NULL,
                     updated_at=excluded.updated_at""",
                (
                    row["id"],
                    "pending",
                    sha,
                    row["chunk_source"],
                    row["analysis_version"],
                    row["chunks_json"],
                    row["correct_order_json"],
                    row["practice_structure_json"],
                    row["furigana_json"],
                    row["chunks_manually_edited"],
                    None,
                    None,
                    None,
                    stamp,
                ),
            )
        conn.execute(
            """UPDATE kwja_migration_items
               SET status='pending',
                   error_message='上次执行在 processing 状态中断，已自动恢复',
                   updated_at=?
               WHERE status='processing'""",
            (stamp,),
        )
    return len(rows)


def process_sentence(sentence_id: int) -> dict:
    started = database.now_iso()
    with database.get_db() as conn:
        conn.execute("BEGIN IMMEDIATE")
        changed = conn.execute(
            """UPDATE kwja_migration_items
               SET status='processing',started_at=?,completed_at=NULL,
                   error_message=NULL,updated_at=?
               WHERE sentence_id=? AND status IN ('pending','failed')""",
            (started, started, sentence_id),
        ).rowcount
        if changed != 1:
            return {"status": "skipped"}
        sentence = conn.execute(
            "SELECT * FROM sentences WHERE id=?", (sentence_id,)
        ).fetchone()
        if not sentence:
            raise ValueError("句子不存在")
        original_text = sentence["japanese"]
        expected_sha = input_sha256(original_text)
        corpus = corpus_readings(conn, exclude_sentence_id=sentence_id)

    try:
        result = analyze_sentence(original_text)
        valid, message = validate_practice_data(
            original_text,
            result["chunks"],
            result["structure"],
            result["correctOrder"],
        )
        if not valid:
            raise ValueError(message)
        analysis = result["analysis"]
        if analysis["inputSha256"] != expected_sha:
            raise ValueError("分析结果原文 SHA256 不一致")
        if "".join(
            original_text[item["start"] : item["end"]]
            for item in result["structure"]
        ) != original_text:
            raise ValueError("练习结构无法逐字还原原句")
        generated, skipped = generate_reading_cards(
            original_text,
            analysis,
            corpus_readings=corpus,
        )
        reasons = Counter(item.get("reason", "unknown") for item in skipped)
        stamp = database.now_iso()
        with database.get_db() as conn:
            conn.execute("BEGIN IMMEDIATE")
            current = conn.execute(
                "SELECT japanese FROM sentences WHERE id=?", (sentence_id,)
            ).fetchone()
            if not current or input_sha256(current["japanese"]) != expected_sha:
                raise RuntimeError("句子在分析期间发生变化")
            conn.execute(
                """UPDATE sentences SET
                     chunks_json=?,correct_order_json=?,
                     practice_structure_json=?,chunk_source=?,
                     chunk_schema_version=?,chunks_manually_edited=0,
                     furigana_json=?,analysis_json=?,analysis_input_sha256=?,
                     analysis_version=?,analysis_updated_at=?,updated_at=?
                   WHERE id=?""",
                (
                    json.dumps(result["chunks"], ensure_ascii=False),
                    json.dumps(result["correctOrder"], ensure_ascii=False),
                    json.dumps(result["structure"], ensure_ascii=False),
                    result["source"],
                    result["schemaVersion"],
                    json.dumps(result["furigana"], ensure_ascii=False),
                    json.dumps(analysis, ensure_ascii=False),
                    analysis["inputSha256"],
                    analysis["analyzerVersion"],
                    stamp,
                    stamp,
                    sentence_id,
                ),
            )
            ensure_sentence_order_card(conn, sentence_id, stamp)
            card_stats = reconcile_reading_cards(
                conn, sentence_id, generated, stamp=stamp
            )
            active_reading_count = conn.execute(
                """SELECT COUNT(*) FROM practice_cards
                   WHERE sentence_id=? AND card_type='kanji_reading'
                     AND active=1""",
                (sentence_id,),
            ).fetchone()[0]
            conn.execute(
                """UPDATE kwja_migration_items SET
                     status='success',new_analysis_source=?,
                     new_analysis_version=?,phrase_count=?,
                     reading_card_count=?,reading_skip_count=?,
                     reading_skip_reasons_json=?,error_message=NULL,
                     completed_at=?,updated_at=?
                   WHERE sentence_id=?""",
                (
                    result["source"],
                    analysis["analyzerVersion"],
                    len(analysis["phrases"]),
                    active_reading_count,
                    len(skipped),
                    json.dumps(reasons, ensure_ascii=False),
                    stamp,
                    stamp,
                    sentence_id,
                ),
            )
        return {
            "status": "success",
            "readingCardCount": active_reading_count,
            "readingSkipCount": len(skipped),
            "created": card_stats["created"],
        }
    except Exception as exc:
        LOGGER.exception("sentence %s migration failed", sentence_id)
        stamp = database.now_iso()
        with database.get_db() as conn:
            conn.execute(
                """UPDATE kwja_migration_items
                   SET status='failed',error_message=?,completed_at=?,
                       updated_at=?
                   WHERE sentence_id=?""",
                (str(exc)[:4000], stamp, stamp, sentence_id),
            )
        return {"status": "failed", "error": str(exc)}


def validate_complete() -> dict:
    with database.get_db() as conn:
        counts = {
            row["status"]: row["n"]
            for row in conn.execute(
                """SELECT status,COUNT(*) n FROM kwja_migration_items
                   GROUP BY status"""
            )
        }
        sentence_count = conn.execute("SELECT COUNT(*) FROM sentences").fetchone()[0]
        order_count = conn.execute(
            """SELECT COUNT(*) FROM practice_cards
               WHERE card_type='sentence_order' AND active=1"""
        ).fetchone()[0]
        reading_count = conn.execute(
            """SELECT COUNT(*) FROM practice_cards
               WHERE card_type='kanji_reading' AND active=1"""
        ).fetchone()[0]
        invalid_reconstruction = 0
        invalid_targets = 0
        invalid_options = 0
        for row in conn.execute(
            """SELECT s.japanese,s.chunks_json,s.correct_order_json,
                      s.practice_structure_json,s.analysis_json
               FROM sentences s
               JOIN kwja_migration_items mi ON mi.sentence_id=s.id
               WHERE mi.status='success'"""
        ):
            chunks = database.json_load(row["chunks_json"], [])
            order = database.json_load(row["correct_order_json"], [])
            structure = database.json_load(row["practice_structure_json"], [])
            valid, _ = validate_practice_data(
                row["japanese"], chunks, structure, order
            )
            if not valid:
                invalid_reconstruction += 1
        for row in conn.execute(
            """SELECT s.japanese,pc.payload_json FROM practice_cards pc
               JOIN sentences s ON s.id=pc.sentence_id
               WHERE pc.card_type='kanji_reading' AND pc.active=1"""
        ):
            from reading_cards import validate_reading_payload

            valid, message = validate_reading_payload(
                row["japanese"],
                database.json_load(row["payload_json"], {}),
            )
            if not valid:
                if "位置" in message:
                    invalid_targets += 1
                else:
                    invalid_options += 1
        fsrs_mismatch = conn.execute(
            """SELECT COUNT(*) FROM sentences s
               JOIN practice_cards pc
                 ON pc.sentence_id=s.id
                AND pc.card_type='sentence_order' AND pc.active=1
               WHERE pc.fsrs_state IS NOT s.fsrs_state
                  OR pc.fsrs_step IS NOT s.fsrs_step
                  OR pc.stability IS NOT s.stability
                  OR pc.difficulty IS NOT s.difficulty
                  OR pc.last_review_at IS NOT s.last_review_at
                  OR pc.next_review_at IS NOT s.next_review_at
                  OR pc.fsrs_version IS NOT s.fsrs_version"""
        ).fetchone()[0]
        foreign_keys = len(conn.execute("PRAGMA foreign_key_check").fetchall())
        result = {
            "sentences": sentence_count,
            "sentenceOrderCards": order_count,
            "kanjiReadingCards": reading_count,
            "statuses": counts,
            "fsrsMismatch": fsrs_mismatch,
            "foreignKeyErrors": foreign_keys,
            "invalidReconstruction": invalid_reconstruction,
            "invalidReadingTargets": invalid_targets,
            "invalidReadingOptions": invalid_options,
        }
        success = (
            counts.get("success", 0) == sentence_count
            and order_count == sentence_count
            and not counts.get("failed", 0)
            and not counts.get("pending", 0)
            and not counts.get("processing", 0)
            and not fsrs_mismatch
            and not foreign_keys
            and not invalid_reconstruction
            and not invalid_targets
            and not invalid_options
        )
        if success:
            conn.execute(
                """INSERT OR IGNORE INTO schema_migrations(version,applied_at)
                   VALUES(?,?)""",
                (database.KWJA_ANALYSIS_MIGRATION, database.now_iso()),
            )
        result["ok"] = success
        return result


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Migrate all sentence analysis and practice cards to KWJA tiny."
    )
    parser.add_argument(
        "--database",
        type=Path,
        default=database.DB_PATH,
        help="SQLite database path",
    )
    parser.add_argument("--schema-only", action="store_true")
    parser.add_argument("--retry-failed", action="store_true")
    parser.add_argument("--force-all", action="store_true")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )
    configure_database(args.database)
    database.init_db(enable_fuzzing=False)
    if args.schema_only:
        print(json.dumps({"ok": True, "schemaOnly": True}, ensure_ascii=False))
        return 0

    total = seed_queue(force_all=args.force_all)
    statuses = ["pending"]
    if args.retry_failed:
        statuses.append("failed")
    placeholders = ",".join("?" for _ in statuses)
    with database.get_db() as conn:
        query = (
            f"""SELECT sentence_id FROM kwja_migration_items
                WHERE status IN ({placeholders}) ORDER BY sentence_id"""
        )
        params: list[object] = list(statuses)
        if args.limit is not None:
            query += " LIMIT ?"
            params.append(max(0, args.limit))
        ids = [row["sentence_id"] for row in conn.execute(query, params)]

    processed = Counter()
    for index, sentence_id in enumerate(ids, start=1):
        result = process_sentence(sentence_id)
        processed[result["status"]] += 1
        LOGGER.info(
            "[%s/%s] sentence=%s status=%s",
            index,
            len(ids),
            sentence_id,
            result["status"],
        )
    validation = validate_complete()
    report = {
        "queueSentenceCount": total,
        "selectedCount": len(ids),
        "processed": dict(processed),
        "validation": validation,
    }
    text = json.dumps(report, ensure_ascii=False, indent=2)
    print(text)
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(text + "\n", encoding="utf-8")
    return 0 if validation["ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
