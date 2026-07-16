from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

DATA_DIR = Path(os.environ.get("DATA_DIR", "data"))
DB_PATH = DATA_DIR / "japanese_sentence_review.sqlite3"


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def get_db():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(DB_PATH, timeout=15)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys=ON")
    db.execute("PRAGMA journal_mode=WAL")
    return db


def _add_column_if_missing(db, table: str, column: str, ddl: str):
    columns = {row["name"] for row in db.execute(f"PRAGMA table_info({table})")}
    if column not in columns:
        db.execute(f"ALTER TABLE {table} ADD COLUMN {ddl}")


def _drop_column_if_exists(db, table: str, column: str):
    """Drop a column if present. Safe under concurrent gunicorn workers."""
    columns = {row["name"] for row in db.execute(f"PRAGMA table_info({table})")}
    if column not in columns:
        return
    try:
        db.execute(f"ALTER TABLE {table} DROP COLUMN {column}")
    except sqlite3.OperationalError as exc:
        # Another worker may have dropped it between PRAGMA and ALTER.
        if "no such column" not in str(exc).lower():
            raise


def _backfill_review_events(db):
    """One-shot: map legacy attempts into review_events when the table is empty.

    Orphaned FKs (deleted sentence/session) are nulled so backfill never crashes
    older production databases.
    """
    event_count = db.execute("SELECT COUNT(*) n FROM review_events").fetchone()["n"]
    if event_count:
        return
    attempt_count = db.execute("SELECT COUNT(*) n FROM attempts").fetchone()["n"]
    if not attempt_count:
        return
    # Legacy mapping: correct→known, wrong→forgotten, skipped→skipped
    # Null out sentence_id / session_id when the referenced row is gone.
    db.execute("""
        INSERT INTO review_events(
          sentence_id, session_id, reviewed_at, result, duration_ms, attempt_n,
          is_new, stability_before, stability_after, interval_days, created_at
        )
        SELECT
          CASE WHEN s.id IS NULL THEN NULL ELSE a.sentence_id END,
          CASE WHEN p.id IS NULL THEN NULL ELSE a.session_id END,
          a.created_at,
          CASE a.status
            WHEN 'correct' THEN 'known'
            WHEN 'wrong' THEN 'forgotten'
            ELSE 'skipped'
          END,
          0,
          1,
          0,
          NULL,
          NULL,
          NULL,
          a.created_at
        FROM attempts a
        LEFT JOIN sentences s ON s.id = a.sentence_id
        LEFT JOIN practice_sessions p ON p.id = a.session_id
    """)


def init_db():
    with get_db() as db:
        db.executescript("""
        CREATE TABLE IF NOT EXISTS settings (
          key TEXT PRIMARY KEY, value TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS collections (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          name TEXT NOT NULL UNIQUE COLLATE NOCASE,
          created_at TEXT NOT NULL, updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS sentences (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          collection_id INTEGER NOT NULL REFERENCES collections(id) ON DELETE RESTRICT,
          chinese TEXT NOT NULL,
          japanese TEXT NOT NULL,
          chunks_json TEXT NOT NULL,
          correct_order_json TEXT NOT NULL,
          furigana_json TEXT NOT NULL DEFAULT '[]',
          study_count INTEGER NOT NULL DEFAULT 0,
          correct_count INTEGER NOT NULL DEFAULT 0,
          wrong_count INTEGER NOT NULL DEFAULT 0,
          skip_count INTEGER NOT NULL DEFAULT 0,
          correct_streak INTEGER NOT NULL DEFAULT 0,
          stability REAL NOT NULL DEFAULT 1.0,
          review_count INTEGER NOT NULL DEFAULT 0,
          lapse_count INTEGER NOT NULL DEFAULT 0,
          next_review_at TEXT NOT NULL,
          created_at TEXT NOT NULL,
          updated_at TEXT NOT NULL,
          last_practiced_at TEXT
        );
        CREATE TABLE IF NOT EXISTS practice_sessions (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          source TEXT NOT NULL DEFAULT 'due',
          sentence_ids_json TEXT NOT NULL,
          total INTEGER NOT NULL,
          correct INTEGER NOT NULL DEFAULT 0,
          wrong INTEGER NOT NULL DEFAULT 0,
          skipped INTEGER NOT NULL DEFAULT 0,
          completed_at TEXT,
          created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS attempts (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          session_id INTEGER NOT NULL REFERENCES practice_sessions(id) ON DELETE CASCADE,
          sentence_id INTEGER REFERENCES sentences(id) ON DELETE SET NULL,
          status TEXT NOT NULL CHECK(status IN ('correct','wrong','skipped')),
          answer_order_json TEXT NOT NULL DEFAULT '[]',
          sentence_snapshot_json TEXT NOT NULL,
          stats_before_json TEXT NOT NULL DEFAULT '{}',
          duration_ms INTEGER NOT NULL DEFAULT 0,
          attempt_n INTEGER NOT NULL DEFAULT 1,
          grade TEXT NOT NULL DEFAULT '',
          created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS review_events (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          sentence_id INTEGER REFERENCES sentences(id) ON DELETE SET NULL,
          session_id INTEGER REFERENCES practice_sessions(id) ON DELETE SET NULL,
          reviewed_at TEXT NOT NULL,
          result TEXT NOT NULL CHECK(result IN (
            'mastered','known','fuzzy','forgotten','skipped'
          )),
          duration_ms INTEGER NOT NULL DEFAULT 0,
          attempt_n INTEGER NOT NULL DEFAULT 1,
          is_new INTEGER NOT NULL DEFAULT 0,
          stability_before REAL,
          stability_after REAL,
          interval_days REAL,
          created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS login_attempts (
          identifier TEXT PRIMARY KEY,
          fail_count INTEGER NOT NULL DEFAULT 0,
          last_failed_at REAL NOT NULL DEFAULT 0,
          locked_until REAL NOT NULL DEFAULT 0
        );
        CREATE INDEX IF NOT EXISTS idx_sentences_collection ON sentences(collection_id);
        CREATE INDEX IF NOT EXISTS idx_sentences_due ON sentences(next_review_at);
        CREATE INDEX IF NOT EXISTS idx_attempts_session ON attempts(session_id);
        CREATE INDEX IF NOT EXISTS idx_attempts_sentence ON attempts(sentence_id);
        CREATE INDEX IF NOT EXISTS idx_sessions_created ON practice_sessions(created_at DESC);
        CREATE INDEX IF NOT EXISTS idx_review_events_reviewed ON review_events(reviewed_at);
        CREATE INDEX IF NOT EXISTS idx_review_events_sentence ON review_events(sentence_id, reviewed_at);
        CREATE INDEX IF NOT EXISTS idx_review_events_session_sentence
          ON review_events(session_id, sentence_id);
        """)
        # Idempotent ALTERs for existing databases
        _add_column_if_missing(db, "attempts", "stats_before_json", "stats_before_json TEXT NOT NULL DEFAULT '{}'")
        _add_column_if_missing(db, "attempts", "duration_ms", "duration_ms INTEGER NOT NULL DEFAULT 0")
        _add_column_if_missing(db, "attempts", "attempt_n", "attempt_n INTEGER NOT NULL DEFAULT 1")
        _add_column_if_missing(db, "attempts", "grade", "grade TEXT NOT NULL DEFAULT ''")
        _add_column_if_missing(db, "sentences", "furigana_json", "furigana_json TEXT NOT NULL DEFAULT '[]'")
        _add_column_if_missing(db, "sentences", "stability", "stability REAL NOT NULL DEFAULT 1.0")
        _add_column_if_missing(db, "sentences", "review_count", "review_count INTEGER NOT NULL DEFAULT 0")
        _add_column_if_missing(db, "sentences", "lapse_count", "lapse_count INTEGER NOT NULL DEFAULT 0")
        # Drop legacy unused sentence metadata columns (always empty in practice).
        _drop_column_if_exists(db, "sentences", "kana")
        _drop_column_if_exists(db, "sentences", "romaji")
        _drop_column_if_exists(db, "sentences", "explanation")

        db.execute("DELETE FROM settings WHERE key IN ('base_url','model','custom_params','api_key_encrypted','scheduler_mode')")
        for row in db.execute("SELECT id,chunks_json FROM sentences").fetchall():
            chunks = json_load(row["chunks_json"], [])
            compact = [{"id": item.get("id"), "text": item.get("text")} for item in chunks if isinstance(item, dict)]
            if compact != chunks:
                db.execute("UPDATE sentences SET chunks_json=? WHERE id=?", (json.dumps(compact, ensure_ascii=False), row["id"]))
        stamp = now_iso()
        db.execute("INSERT OR IGNORE INTO collections(name,created_at,updated_at) VALUES('默认句集',?,?)", (stamp, stamp))
        _backfill_review_events(db)
        # Purge history left by older ON DELETE SET NULL behavior (hard-delete is now app policy).
        _purge_orphaned_sentence_history(db)
        _migrate_mastered_to_known(db)


def _migrate_mastered_to_known(db):
    """One-shot: fold legacy mastered grades into known (new taxonomy).

    Old "熟知" + "认识" both map to "认识". CHECK still allows 'mastered' so we
    avoid rebuilding the table; new code never writes mastered.
    """
    db.execute("UPDATE review_events SET result='known' WHERE result='mastered'")
    db.execute("UPDATE attempts SET grade='known' WHERE grade='mastered'")


def _purge_orphaned_sentence_history(db):
    """Remove review_events/attempts that no longer belong to any sentence.

    The API hard-deletes these rows when a sentence is removed; this cleans
    leftovers from older SET NULL FK behavior and any dangling FKs.
    """
    db.execute("DELETE FROM review_events WHERE sentence_id IS NULL")
    db.execute(
        """DELETE FROM review_events
           WHERE sentence_id IS NOT NULL
             AND NOT EXISTS (SELECT 1 FROM sentences s WHERE s.id = review_events.sentence_id)"""
    )
    db.execute("DELETE FROM attempts WHERE sentence_id IS NULL")
    db.execute(
        """DELETE FROM attempts
           WHERE sentence_id IS NOT NULL
             AND NOT EXISTS (SELECT 1 FROM sentences s WHERE s.id = attempts.sentence_id)"""
    )


def setting(db, key, default=""):
    row = db.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    return row["value"] if row else default


def set_setting(db, key, value):
    db.execute("INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, value))


def json_load(value, fallback=None):
    try:
        return json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return fallback
