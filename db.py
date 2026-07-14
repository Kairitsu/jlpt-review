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
          kana TEXT NOT NULL DEFAULT '',
          romaji TEXT NOT NULL DEFAULT '',
          explanation TEXT NOT NULL DEFAULT '',
          study_count INTEGER NOT NULL DEFAULT 0,
          correct_count INTEGER NOT NULL DEFAULT 0,
          wrong_count INTEGER NOT NULL DEFAULT 0,
          skip_count INTEGER NOT NULL DEFAULT 0,
          correct_streak INTEGER NOT NULL DEFAULT 0,
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
        """)
        columns = {row["name"] for row in db.execute("PRAGMA table_info(attempts)")}
        if "stats_before_json" not in columns:
            db.execute("ALTER TABLE attempts ADD COLUMN stats_before_json TEXT NOT NULL DEFAULT '{}'")
        sentence_columns = {row["name"] for row in db.execute("PRAGMA table_info(sentences)")}
        if "furigana_json" not in sentence_columns:
            db.execute("ALTER TABLE sentences ADD COLUMN furigana_json TEXT NOT NULL DEFAULT '[]'")
        db.execute("DELETE FROM settings WHERE key IN ('base_url','model','custom_params','api_key_encrypted')")
        for row in db.execute("SELECT id,chunks_json FROM sentences").fetchall():
            chunks = json_load(row["chunks_json"], [])
            compact = [{"id": item.get("id"), "text": item.get("text")} for item in chunks if isinstance(item, dict)]
            if compact != chunks:
                db.execute("UPDATE sentences SET chunks_json=? WHERE id=?", (json.dumps(compact, ensure_ascii=False), row["id"]))
        db.execute("UPDATE sentences SET kana='',romaji='',explanation='' WHERE kana<>'' OR romaji<>'' OR explanation<>''")
        stamp = now_iso()
        db.execute("INSERT OR IGNORE INTO collections(name,created_at,updated_at) VALUES('默认句集',?,?)", (stamp, stamp))


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
