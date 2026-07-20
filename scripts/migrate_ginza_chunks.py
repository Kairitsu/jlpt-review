#!/usr/bin/env python3
"""Safely migrate saved sentence chunks to GiNZA fixed/slot schema v2."""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import sqlite3
import sys
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from db import GINZA_CHUNKS_MIGRATION  # noqa: E402
from tokenizer import (  # noqa: E402
    CHUNK_SCHEMA_VERSION,
    analyze_sentence,
    is_fixed_char,
    reconstruct_sentence,
    structure_from_manual_chunks,
    validate_practice_data,
)


LOGGER = logging.getLogger("ginza_chunk_migration")
DERIVED_SENTENCE_COLUMNS = {
    "chunks_json",
    "correct_order_json",
    "practice_structure_json",
    "chunk_source",
    "chunk_schema_version",
    "chunks_manually_edited",
}
PROTECTED_TABLES = ("collections", "practice_sessions", "practice_items", "attempts", "review_events")


@dataclass
class MigrationStats:
    total: int = 0
    migrated: int = 0
    manual_preserved: int = 0
    ginza: int = 0
    fallback: int = 0
    skipped: int = 0
    failed: int = 0


def utc_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")


def configure_logging(log_file: Path | None) -> None:
    handlers: list[logging.Handler] = [logging.StreamHandler(sys.stdout)]
    if log_file:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        handlers.append(logging.FileHandler(log_file, encoding="utf-8"))
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=handlers,
        force=True,
    )


def connect(path: Path, *, read_only: bool = False) -> sqlite3.Connection:
    if read_only:
        db = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=30)
    else:
        db = sqlite3.connect(path, timeout=30)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys=ON")
    if not read_only:
        db.execute("PRAGMA journal_mode=WAL")
    return db


def backup_database(source_path: Path, backup_dir: Path) -> Path:
    backup_dir.mkdir(parents=True, exist_ok=True)
    target = backup_dir / f"{source_path.stem}-pre-ginza-{utc_stamp()}.sqlite3"
    with connect(source_path, read_only=True) as source, sqlite3.connect(target) as backup:
        source.backup(backup)
        integrity = backup.execute("PRAGMA integrity_check").fetchone()[0]
        if integrity != "ok":
            raise sqlite3.DatabaseError(f"backup integrity check failed: {integrity}")
    LOGGER.info("backup=%s", target)
    return target


def table_columns(db: sqlite3.Connection, table: str) -> list[str]:
    return [row["name"] for row in db.execute(f"PRAGMA table_info({table})")]


def table_hash(db: sqlite3.Connection, table: str, columns: list[str] | None = None) -> str:
    columns = columns or table_columns(db, table)
    digest = hashlib.sha256()
    select = ",".join(f'"{column}"' for column in columns)
    for row in db.execute(f"SELECT {select} FROM {table} ORDER BY rowid"):
        values = [row[column] for column in columns]
        digest.update(json.dumps(values, ensure_ascii=False, separators=(",", ":"), default=str).encode())
        digest.update(b"\n")
    return digest.hexdigest()


def protected_snapshot(db: sqlite3.Connection) -> dict:
    integrity = db.execute("PRAGMA integrity_check").fetchone()[0]
    sentence_columns = [
        column for column in table_columns(db, "sentences")
        if column not in DERIVED_SENTENCE_COLUMNS
    ]
    counts = {
        table: int(db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
        for table in ("sentences", *PROTECTED_TABLES)
    }
    return {
        "integrity": integrity,
        "counts": counts,
        "hashes": {
            "sentences_protected": table_hash(db, "sentences", sentence_columns),
            **{table: table_hash(db, table) for table in PROTECTED_TABLES},
        },
        "fsrs_cards": counts["sentences"],
        "review_events": counts["review_events"],
    }


def ensure_schema(db: sqlite3.Connection) -> None:
    columns = set(table_columns(db, "sentences"))
    additions = (
        ("practice_structure_json", "TEXT NOT NULL DEFAULT '[]'"),
        ("chunk_source", "TEXT NOT NULL DEFAULT 'legacy'"),
        ("chunk_schema_version", "INTEGER NOT NULL DEFAULT 1"),
        ("chunks_manually_edited", "INTEGER NOT NULL DEFAULT 0 CHECK(chunks_manually_edited IN (0,1))"),
    )
    for column, definition in additions:
        if column not in columns:
            db.execute(f"ALTER TABLE sentences ADD COLUMN {column} {definition}")


def old_chunks_look_manual(chunks) -> bool:
    """Legacy browser-created IDs contain hyphens; mixed fixed chunks are also evidence."""
    if not isinstance(chunks, list):
        return False
    for item in chunks:
        if not isinstance(item, dict):
            continue
        identifier, text = item.get("id"), item.get("text")
        if isinstance(identifier, str) and "-" in identifier:
            return True
        if isinstance(text, str) and text:
            fixed = [is_fixed_char(char) for char in text]
            if any(fixed) and not all(fixed):
                return True
    return False


def row_is_current(row: sqlite3.Row) -> bool:
    if int(row["chunk_schema_version"] or 0) != CHUNK_SCHEMA_VERSION:
        return False
    chunks = json.loads(row["chunks_json"])
    order = json.loads(row["correct_order_json"])
    structure = json.loads(row["practice_structure_json"])
    return validate_practice_data(row["japanese"], chunks, structure, order)[0]


def migrate_rows(db: sqlite3.Connection, *, preserve_manual: bool) -> MigrationStats:
    stats = MigrationStats()
    rows = db.execute("SELECT * FROM sentences ORDER BY id").fetchall()
    stats.total = len(rows)
    for row in rows:
        sentence_id = row["id"]
        try:
            if row_is_current(row):
                stats.skipped += 1
                continue
        except (TypeError, ValueError, json.JSONDecodeError):
            pass

        db.execute("SAVEPOINT migrate_sentence")
        try:
            old_chunks = json.loads(row["chunks_json"])
            explicit_manual = bool(row["chunks_manually_edited"]) or str(row["chunk_source"]).startswith("manual")
            manual = preserve_manual and (explicit_manual or old_chunks_look_manual(old_chunks))
            if manual:
                chunks, structure = structure_from_manual_chunks(row["japanese"], old_chunks)
                source = "manual_migrated"
                manually_edited = 1
                stats.manual_preserved += 1
            else:
                analysis = analyze_sentence(row["japanese"])
                chunks = analysis["chunks"]
                structure = analysis["structure"]
                source = analysis["source"]
                manually_edited = 0
                if source == "fallback":
                    stats.fallback += 1
                else:
                    stats.ginza += 1
            order = [chunk["id"] for chunk in chunks]
            valid, message = validate_practice_data(row["japanese"], chunks, structure, order)
            if not valid:
                raise ValueError(message)
            db.execute(
                """UPDATE sentences
                   SET chunks_json=?,correct_order_json=?,practice_structure_json=?,
                       chunk_source=?,chunk_schema_version=?,chunks_manually_edited=?
                   WHERE id=?""",
                (
                    json.dumps(chunks, ensure_ascii=False, separators=(",", ":")),
                    json.dumps(order, ensure_ascii=False, separators=(",", ":")),
                    json.dumps(structure, ensure_ascii=False, separators=(",", ":")),
                    source, CHUNK_SCHEMA_VERSION, manually_edited, sentence_id,
                ),
            )
            db.execute("RELEASE SAVEPOINT migrate_sentence")
            stats.migrated += 1
            LOGGER.info("sentence=%s source=%s slots=%s", sentence_id, source, len(chunks))
        except Exception as exc:
            db.execute("ROLLBACK TO SAVEPOINT migrate_sentence")
            db.execute("RELEASE SAVEPOINT migrate_sentence")
            stats.failed += 1
            LOGGER.exception("sentence=%s failed=%s", sentence_id, exc)
    return stats


def validate_all_rows(db: sqlite3.Connection) -> dict:
    failures = []
    punctuation_chunks = 0
    fallback_rows = 0
    for row in db.execute("SELECT * FROM sentences ORDER BY id"):
        try:
            chunks = json.loads(row["chunks_json"])
            order = json.loads(row["correct_order_json"])
            structure = json.loads(row["practice_structure_json"])
            valid, message = validate_practice_data(row["japanese"], chunks, structure, order)
            if not valid:
                failures.append({"id": row["id"], "error": message})
                continue
            if reconstruct_sentence(chunks, structure) != row["japanese"]:
                failures.append({"id": row["id"], "error": "reconstruction mismatch"})
            punctuation_chunks += sum(any(is_fixed_char(char) for char in item["text"]) for item in chunks)
            fallback_rows += int(row["chunk_source"] == "fallback")
        except Exception as exc:
            failures.append({"id": row["id"], "error": str(exc)})
    return {
        "valid": not failures and punctuation_chunks == 0,
        "failures": failures,
        "punctuation_chunks": punctuation_chunks,
        "fallback_rows": fallback_rows,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--apply", action="store_true", help="write changes; otherwise audit only")
    parser.add_argument("--backup-dir", type=Path)
    parser.add_argument("--log-file", type=Path)
    parser.add_argument("--overwrite-manual", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    database = args.database.resolve()
    log_file = args.log_file or database.parent / "backups" / f"ginza-migration-{utc_stamp()}.log"
    configure_logging(log_file)
    if not database.is_file():
        LOGGER.error("database does not exist: %s", database)
        return 2

    with connect(database, read_only=not args.apply) as db:
        before = protected_snapshot(db)
    LOGGER.info("before=%s", json.dumps(before, ensure_ascii=False, sort_keys=True))
    if not args.apply:
        LOGGER.info("audit-only: pass --apply to migrate")
        print(json.dumps({"applied": False, "before": before, "log": str(log_file)}, ensure_ascii=False))
        return 0

    backup = backup_database(database, (args.backup_dir or database.parent / "backups").resolve())
    with connect(database) as db:
        db.execute("BEGIN IMMEDIATE")
        ensure_schema(db)
        stats = migrate_rows(db, preserve_manual=not args.overwrite_manual)
        validation = validate_all_rows(db)
        if stats.failed or not validation["valid"]:
            db.rollback()
            LOGGER.error("migration rolled back: stats=%s validation=%s", asdict(stats), validation)
            print(json.dumps({
                "applied": False, "rolledBack": True, "backup": str(backup),
                "stats": asdict(stats), "validation": validation, "before": before,
                "log": str(log_file),
            }, ensure_ascii=False))
            return 1
        db.execute(
            "INSERT OR REPLACE INTO schema_migrations(version,applied_at) VALUES(?,?)",
            (GINZA_CHUNKS_MIGRATION, datetime.now(timezone.utc).isoformat(timespec="seconds")),
        )
        db.commit()

    with connect(database, read_only=True) as db:
        after = protected_snapshot(db)
        validation = validate_all_rows(db)
    protected_equal = before["counts"] == after["counts"] and before["hashes"] == after["hashes"]
    ok = protected_equal and after["integrity"] == "ok" and validation["valid"]
    summary = {
        "applied": True,
        "ok": ok,
        "backup": str(backup),
        "log": str(log_file),
        "stats": asdict(stats),
        "before": before,
        "after": after,
        "protectedEqual": protected_equal,
        "validation": validation,
    }
    LOGGER.info("summary=%s", json.dumps(summary, ensure_ascii=False, sort_keys=True))
    print(json.dumps(summary, ensure_ascii=False))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
