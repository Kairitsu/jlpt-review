from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from fsrs_service import FSRS_VERSION

DATA_DIR = Path(os.environ.get("DATA_DIR", "data"))
DB_PATH = DATA_DIR / "japanese_sentence_review.sqlite3"
FSRS_MIGRATION = "fsrs_v1_reset"


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def get_db():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(DB_PATH, timeout=15)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys=ON")
    db.execute("PRAGMA journal_mode=WAL")
    return db


def _table_exists(db, name: str) -> bool:
    return db.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone() is not None


def _columns(db, table: str) -> set[str]:
    return {row["name"] for row in db.execute(f"PRAGMA table_info({table})")}


def _create_base_tables(db) -> None:
    db.execute("""CREATE TABLE IF NOT EXISTS schema_migrations (
        version TEXT PRIMARY KEY, applied_at TEXT NOT NULL
    )""")
    db.execute("""CREATE TABLE IF NOT EXISTS settings (
        key TEXT PRIMARY KEY, value TEXT NOT NULL
    )""")
    db.execute("""CREATE TABLE IF NOT EXISTS collections (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL UNIQUE COLLATE NOCASE,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )""")
    db.execute("""CREATE TABLE IF NOT EXISTS login_attempts (
        identifier TEXT PRIMARY KEY,
        fail_count INTEGER NOT NULL DEFAULT 0,
        last_failed_at REAL NOT NULL DEFAULT 0,
        locked_until REAL NOT NULL DEFAULT 0
    )""")


def _create_sentences_table(db) -> None:
    db.execute("""CREATE TABLE IF NOT EXISTS sentences (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        collection_id INTEGER NOT NULL REFERENCES collections(id) ON DELETE RESTRICT,
        chinese TEXT NOT NULL,
        japanese TEXT NOT NULL,
        chunks_json TEXT NOT NULL,
        correct_order_json TEXT NOT NULL,
        furigana_json TEXT NOT NULL DEFAULT '[]',
        fsrs_state INTEGER NOT NULL DEFAULT 1 CHECK(fsrs_state IN (1,2,3)),
        fsrs_step INTEGER,
        stability REAL,
        difficulty REAL,
        last_review_at TEXT,
        next_review_at TEXT NOT NULL,
        fsrs_version TEXT NOT NULL,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )""")


def _create_review_tables(db) -> None:
    db.execute("""CREATE TABLE IF NOT EXISTS practice_sessions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        source TEXT NOT NULL DEFAULT 'due',
        sentence_ids_json TEXT NOT NULL,
        total INTEGER NOT NULL,
        correct INTEGER NOT NULL DEFAULT 0,
        wrong INTEGER NOT NULL DEFAULT 0,
        skipped INTEGER NOT NULL DEFAULT 0,
        completed_at TEXT,
        report_deleted_at TEXT,
        created_at TEXT NOT NULL
    )""")
    db.execute("""CREATE TABLE IF NOT EXISTS practice_items (
        session_id INTEGER NOT NULL REFERENCES practice_sessions(id) ON DELETE CASCADE,
        sentence_id INTEGER NOT NULL REFERENCES sentences(id) ON DELETE CASCADE,
        position INTEGER NOT NULL,
        finalized_at TEXT,
        final_status TEXT CHECK(final_status IN ('correct','wrong','skipped')),
        fsrs_rating INTEGER CHECK(fsrs_rating BETWEEN 1 AND 4),
        easy_selected INTEGER NOT NULL DEFAULT 0 CHECK(easy_selected IN (0,1)),
        PRIMARY KEY(session_id, sentence_id),
        UNIQUE(session_id, position)
    )""")
    db.execute("""CREATE TABLE IF NOT EXISTS attempts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        session_id INTEGER NOT NULL REFERENCES practice_sessions(id) ON DELETE CASCADE,
        sentence_id INTEGER NOT NULL REFERENCES sentences(id) ON DELETE CASCADE,
        status TEXT NOT NULL CHECK(status IN ('correct','wrong','skipped')),
        answer_order_json TEXT NOT NULL DEFAULT '[]',
        sentence_snapshot_json TEXT NOT NULL,
        duration_ms INTEGER NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL
    )""")
    db.execute("""CREATE TABLE IF NOT EXISTS review_events (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        sentence_id INTEGER NOT NULL REFERENCES sentences(id) ON DELETE CASCADE,
        session_id INTEGER NOT NULL REFERENCES practice_sessions(id) ON DELETE RESTRICT,
        rating INTEGER NOT NULL CHECK(rating BETWEEN 1 AND 4),
        reviewed_at TEXT NOT NULL,
        duration_ms INTEGER NOT NULL DEFAULT 0,
        is_new INTEGER NOT NULL DEFAULT 0 CHECK(is_new IN (0,1)),
        fsrs_state_before INTEGER NOT NULL,
        fsrs_state_after INTEGER NOT NULL,
        fsrs_step_before INTEGER,
        fsrs_step_after INTEGER,
        stability_before REAL,
        stability_after REAL,
        difficulty_before REAL,
        difficulty_after REAL,
        next_review_before TEXT NOT NULL,
        next_review_after TEXT NOT NULL,
        fsrs_version TEXT NOT NULL,
        created_at TEXT NOT NULL,
        UNIQUE(session_id, sentence_id)
    )""")


def _create_indexes(db) -> None:
    statements = (
        "CREATE INDEX IF NOT EXISTS idx_sentences_collection ON sentences(collection_id)",
        "CREATE INDEX IF NOT EXISTS idx_sentences_due ON sentences(next_review_at)",
        "CREATE INDEX IF NOT EXISTS idx_attempts_session_sentence ON attempts(session_id, sentence_id, id)",
        "CREATE INDEX IF NOT EXISTS idx_attempts_sentence ON attempts(sentence_id)",
        "CREATE INDEX IF NOT EXISTS idx_sessions_created ON practice_sessions(created_at DESC)",
        "CREATE INDEX IF NOT EXISTS idx_review_events_reviewed ON review_events(reviewed_at)",
        "CREATE INDEX IF NOT EXISTS idx_review_events_sentence ON review_events(sentence_id, reviewed_at)",
    )
    for statement in statements:
        db.execute(statement)


def _reset_legacy_to_fsrs(db, stamp: str) -> None:
    """Destructively replace legacy progress while preserving sentence content."""
    for table in ("review_events", "attempts", "practice_items", "practice_sessions"):
        db.execute(f"DROP TABLE IF EXISTS {table}")

    if _table_exists(db, "sentences"):
        legacy_columns = _columns(db, "sentences")
        db.execute("ALTER TABLE sentences RENAME TO sentences_before_fsrs")
        _create_sentences_table(db)
        furigana = "furigana_json" if "furigana_json" in legacy_columns else "'[]'"
        db.execute(f"""INSERT INTO sentences(
            id, collection_id, chinese, japanese, chunks_json, correct_order_json,
            furigana_json, fsrs_state, fsrs_step, stability, difficulty,
            last_review_at, next_review_at, fsrs_version, created_at, updated_at
        ) SELECT
            id, collection_id, chinese, japanese, chunks_json, correct_order_json,
            {furigana}, 1, 0, NULL, NULL, NULL, ?, ?, created_at, updated_at
          FROM sentences_before_fsrs""", (stamp, FSRS_VERSION))
        db.execute("DROP TABLE sentences_before_fsrs")
    else:
        _create_sentences_table(db)

    _create_review_tables(db)


def init_db() -> None:
    with get_db() as db:
        db.execute("BEGIN IMMEDIATE")
        _create_base_tables(db)
        migrated = db.execute(
            "SELECT 1 FROM schema_migrations WHERE version=?", (FSRS_MIGRATION,)
        ).fetchone()
        stamp = now_iso()
        if not migrated:
            _reset_legacy_to_fsrs(db, stamp)
            db.execute(
                "INSERT INTO schema_migrations(version,applied_at) VALUES(?,?)",
                (FSRS_MIGRATION, stamp),
            )
        else:
            _create_sentences_table(db)
            _create_review_tables(db)
        _create_indexes(db)

        db.execute("DELETE FROM settings WHERE key IN ('scheduler_mode','base_url','model','custom_params','api_key_encrypted')")
        for row in db.execute("SELECT id,chunks_json FROM sentences").fetchall():
            chunks = json_load(row["chunks_json"], [])
            compact = [
                {"id": item.get("id"), "text": item.get("text")}
                for item in chunks if isinstance(item, dict)
            ]
            if compact != chunks:
                db.execute(
                    "UPDATE sentences SET chunks_json=? WHERE id=?",
                    (json.dumps(compact, ensure_ascii=False), row["id"]),
                )
        db.execute(
            "INSERT OR IGNORE INTO collections(name,created_at,updated_at) VALUES('默认句集',?,?)",
            (stamp, stamp),
        )


def setting(db, key, default=""):
    row = db.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    return row["value"] if row else default


def set_setting(db, key, value):
    db.execute(
        "INSERT INTO settings(key,value) VALUES(?,?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, value),
    )


def json_load(value, fallback=None):
    try:
        return json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return fallback
