from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from fsrs_service import FSRS_VERSION, attempt_facts, reschedule_from_review_events

DATA_DIR = Path(os.environ.get("DATA_DIR", "data"))
DB_PATH = DATA_DIR / "japanese_sentence_review.sqlite3"
FSRS_MIGRATION = "fsrs_v1_reset"
NO_SHORT_STEPS_MIGRATION = "fsrs_no_short_steps_v1"
FSRS_RETENTION_098_MIGRATION = "fsrs_retention_098_v1"
UNANSWERED_REPORT_MIGRATION = "practice_unanswered_v1"
COMPLETION_MODE_MIGRATION = "practice_completion_mode_v1"
AUTOMATIC_RATING_MIGRATION = "fsrs_automatic_rating_v2"
GINZA_CHUNKS_MIGRATION = "ginza_bunsetu_chunks_v1"
SENTENCE_NOTE_MIGRATION = "sentence_note_v1"


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
        note TEXT NOT NULL DEFAULT '',
        japanese TEXT NOT NULL,
        chunks_json TEXT NOT NULL,
        correct_order_json TEXT NOT NULL,
        practice_structure_json TEXT NOT NULL DEFAULT '[]',
        chunk_source TEXT NOT NULL DEFAULT 'legacy',
        chunk_schema_version INTEGER NOT NULL DEFAULT 1,
        chunks_manually_edited INTEGER NOT NULL DEFAULT 0
            CHECK(chunks_manually_edited IN (0,1)),
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
        unanswered INTEGER NOT NULL DEFAULT 0,
        completion_mode TEXT NOT NULL DEFAULT 'normal'
            CHECK(completion_mode IN ('normal','early_exit')),
        completed_at TEXT,
        report_deleted_at TEXT,
        created_at TEXT NOT NULL
    )""")
    db.execute("""CREATE TABLE IF NOT EXISTS practice_items (
        session_id INTEGER NOT NULL REFERENCES practice_sessions(id) ON DELETE CASCADE,
        sentence_id INTEGER NOT NULL REFERENCES sentences(id) ON DELETE CASCADE,
        position INTEGER NOT NULL,
        finalized_at TEXT,
        unanswered_at TEXT,
        final_status TEXT CHECK(final_status IN ('correct','wrong','skipped')),
        fsrs_rating INTEGER CHECK(fsrs_rating BETWEEN 1 AND 4),
        easy_selected INTEGER NOT NULL DEFAULT 0 CHECK(easy_selected IN (0,1)),
        draft_answer_order_json TEXT NOT NULL DEFAULT '[]',
        sentence_snapshot_json TEXT,
        PRIMARY KEY(session_id, sentence_id),
        UNIQUE(session_id, position)
    )""")
    db.execute("""CREATE TABLE IF NOT EXISTS attempts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        session_id INTEGER NOT NULL REFERENCES practice_sessions(id) ON DELETE CASCADE,
        sentence_id INTEGER NOT NULL REFERENCES sentences(id) ON DELETE CASCADE,
        client_attempt_id TEXT,
        attempt_number INTEGER NOT NULL CHECK(attempt_number > 0),
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
        attempt_count INTEGER NOT NULL DEFAULT 0 CHECK(attempt_count >= 0),
        first_attempt_correct INTEGER CHECK(first_attempt_correct IN (0,1)),
        second_attempt_correct INTEGER CHECK(second_attempt_correct IN (0,1)),
        final_attempt_correct INTEGER CHECK(final_attempt_correct IN (0,1)),
        rating_policy_version INTEGER NOT NULL DEFAULT 1
            CHECK(rating_policy_version > 0),
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
        "CREATE UNIQUE INDEX IF NOT EXISTS ux_attempts_client_id ON attempts(client_attempt_id) WHERE client_attempt_id IS NOT NULL",
        "CREATE UNIQUE INDEX IF NOT EXISTS ux_attempts_number ON attempts(session_id,sentence_id,attempt_number)",
        "CREATE INDEX IF NOT EXISTS idx_sessions_created ON practice_sessions(created_at DESC)",
        "CREATE INDEX IF NOT EXISTS idx_review_events_reviewed ON review_events(reviewed_at)",
        "CREATE INDEX IF NOT EXISTS idx_review_events_sentence ON review_events(sentence_id, reviewed_at)",
        "CREATE INDEX IF NOT EXISTS idx_practice_items_unanswered ON practice_items(session_id, unanswered_at)",
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


def _migrate_unanswered_reports(db, stamp: str) -> None:
    """Add unanswered-report persistence without rewriting or clearing history."""
    session_columns = _columns(db, "practice_sessions")
    if "unanswered" not in session_columns:
        db.execute(
            "ALTER TABLE practice_sessions ADD COLUMN unanswered INTEGER NOT NULL DEFAULT 0"
        )

    item_columns = _columns(db, "practice_items")
    if "unanswered_at" not in item_columns:
        db.execute("ALTER TABLE practice_items ADD COLUMN unanswered_at TEXT")
    if "draft_answer_order_json" not in item_columns:
        db.execute(
            "ALTER TABLE practice_items ADD COLUMN draft_answer_order_json TEXT NOT NULL DEFAULT '[]'"
        )
    if "sentence_snapshot_json" not in item_columns:
        db.execute("ALTER TABLE practice_items ADD COLUMN sentence_snapshot_json TEXT")

    db.execute(
        "INSERT OR IGNORE INTO schema_migrations(version,applied_at) VALUES(?,?)",
        (UNANSWERED_REPORT_MIGRATION, stamp),
    )


def _migrate_completion_mode(db, stamp: str) -> None:
    """Add an explicit normal/early-exit report marker without touching history."""
    if "completion_mode" not in _columns(db, "practice_sessions"):
        db.execute(
            "ALTER TABLE practice_sessions ADD COLUMN "
            "completion_mode TEXT NOT NULL DEFAULT 'normal'"
        )
    db.execute(
        "INSERT OR IGNORE INTO schema_migrations(version,applied_at) VALUES(?,?)",
        (COMPLETION_MODE_MIGRATION, stamp),
    )


def _migrate_chunk_schema(db) -> None:
    """Add derived GiNZA practice fields without rewriting sentence data."""
    sentence_columns = _columns(db, "sentences")
    additions = (
        ("practice_structure_json", "TEXT NOT NULL DEFAULT '[]'"),
        ("chunk_source", "TEXT NOT NULL DEFAULT 'legacy'"),
        ("chunk_schema_version", "INTEGER NOT NULL DEFAULT 1"),
        (
            "chunks_manually_edited",
            "INTEGER NOT NULL DEFAULT 0 CHECK(chunks_manually_edited IN (0,1))",
        ),
    )
    for column, definition in additions:
        if column not in sentence_columns:
            db.execute(f"ALTER TABLE sentences ADD COLUMN {column} {definition}")


def _migrate_sentence_note(db, stamp: str) -> None:
    """Add optional sentence notes without rebuilding or rewriting sentences."""
    if "note" not in _columns(db, "sentences"):
        db.execute("ALTER TABLE sentences ADD COLUMN note TEXT NOT NULL DEFAULT ''")
    db.execute(
        "INSERT OR IGNORE INTO schema_migrations(version,applied_at) VALUES(?,?)",
        (SENTENCE_NOTE_MIGRATION, stamp),
    )


def _migrate_automatic_rating(db, stamp: str) -> None:
    """Add v2 audit facts and idempotency keys without changing old ratings."""
    attempt_columns = _columns(db, "attempts")
    if "client_attempt_id" not in attempt_columns:
        db.execute("ALTER TABLE attempts ADD COLUMN client_attempt_id TEXT")
    if "attempt_number" not in attempt_columns:
        db.execute("ALTER TABLE attempts ADD COLUMN attempt_number INTEGER")

    # Existing attempt ids already provide a reliable per-item order. Preserve
    # every row and assign that order once so new writes can continue from it.
    db.execute(
        """UPDATE attempts AS target
           SET attempt_number=(
             SELECT COUNT(*) FROM attempts AS preceding
             WHERE preceding.session_id=target.session_id
               AND preceding.sentence_id=target.sentence_id
               AND preceding.id<=target.id
           )
           WHERE attempt_number IS NULL"""
    )

    event_columns = _columns(db, "review_events")
    additions = (
        ("attempt_count", "INTEGER NOT NULL DEFAULT 0 CHECK(attempt_count >= 0)"),
        ("first_attempt_correct", "INTEGER CHECK(first_attempt_correct IN (0,1))"),
        ("second_attempt_correct", "INTEGER CHECK(second_attempt_correct IN (0,1))"),
        ("final_attempt_correct", "INTEGER CHECK(final_attempt_correct IN (0,1))"),
        ("rating_policy_version", "INTEGER NOT NULL DEFAULT 1 CHECK(rating_policy_version > 0)"),
    )
    for column, definition in additions:
        if column not in event_columns:
            db.execute(f"ALTER TABLE review_events ADD COLUMN {column} {definition}")

    # Old ratings remain immutable. Only backfill raw facts where the original
    # ordered check rows make them directly auditable.
    legacy_events = db.execute(
        """SELECT id,session_id,sentence_id FROM review_events
           WHERE attempt_count=0 AND first_attempt_correct IS NULL"""
    ).fetchall()
    for event in legacy_events:
        attempts = db.execute(
            """SELECT status FROM attempts
               WHERE session_id=? AND sentence_id=?
               ORDER BY attempt_number,id""",
            (event["session_id"], event["sentence_id"]),
        ).fetchall()
        facts = attempt_facts(attempts)
        if not facts.attempt_count:
            continue
        db.execute(
            """UPDATE review_events
               SET attempt_count=?,first_attempt_correct=?,
                   second_attempt_correct=?,final_attempt_correct=?
               WHERE id=?""",
            (
                facts.attempt_count,
                int(facts.first_attempt_correct),
                None if facts.second_attempt_correct is None else int(facts.second_attempt_correct),
                int(facts.final_attempt_correct),
                event["id"],
            ),
        )

    db.execute(
        "INSERT OR IGNORE INTO schema_migrations(version,applied_at) VALUES(?,?)",
        (AUTOMATIC_RATING_MIGRATION, stamp),
    )


def _backup_database_for_migration(db, version: str) -> Path:
    """Create a committed SQLite snapshot before a data-changing migration."""
    backup_dir = DATA_DIR / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
    target = backup_dir / f"{DB_PATH.stem}-{version}-{stamp}.sqlite3"
    with sqlite3.connect(target) as backup:
        db.backup(backup)
        integrity = backup.execute("PRAGMA integrity_check").fetchone()[0]
        if integrity != "ok":
            raise sqlite3.DatabaseError(f"backup integrity check failed: {integrity}")
    return target


def _migrate_no_short_steps(*, enable_fuzzing: bool) -> Path | None:
    """Replay FSRS history without learning/relearning steps, exactly once."""
    with get_db() as db:
        migrated = db.execute(
            "SELECT 1 FROM schema_migrations WHERE version=?",
            (NO_SHORT_STEPS_MIGRATION,),
        ).fetchone()
        if migrated:
            return None

        backup_path = _backup_database_for_migration(db, NO_SHORT_STEPS_MIGRATION)
        db.execute("BEGIN IMMEDIATE")
        migrated = db.execute(
            "SELECT 1 FROM schema_migrations WHERE version=?",
            (NO_SHORT_STEPS_MIGRATION,),
        ).fetchone()
        if migrated:
            return backup_path

        stamp = now_iso()
        sentences = db.execute(
            """SELECT s.* FROM sentences s
               WHERE EXISTS(
                 SELECT 1 FROM review_events re WHERE re.sentence_id=s.id
               )
               ORDER BY s.id"""
        ).fetchall()
        for sentence in sentences:
            events = db.execute(
                """SELECT rating,reviewed_at,duration_ms
                   FROM review_events
                   WHERE sentence_id=?
                   ORDER BY reviewed_at,id""",
                (sentence["id"],),
            ).fetchall()
            after = reschedule_from_review_events(
                dict(sentence),
                events,
                enable_fuzzing=enable_fuzzing,
            )
            db.execute(
                """UPDATE sentences
                   SET fsrs_state=?,fsrs_step=?,stability=?,difficulty=?,
                       last_review_at=?,next_review_at=?,fsrs_version=?
                   WHERE id=?""",
                (
                    after["fsrs_state"], after["fsrs_step"], after["stability"],
                    after["difficulty"], after["last_review_at"],
                    after["next_review_at"], after["fsrs_version"], sentence["id"],
                ),
            )
        db.execute(
            "INSERT INTO schema_migrations(version,applied_at) VALUES(?,?)",
            (NO_SHORT_STEPS_MIGRATION, stamp),
        )
        return backup_path


def _migrate_fsrs_retention_098(*, enable_fuzzing: bool) -> Path | None:
    """Replay FSRS history at desired_retention=0.98, exactly once.

    Runs after the no-short-steps migration so cards are already free of
    learning/relearning minute steps.  Only sentence scheduling fields change;
    review_events ratings and all practice history stay immutable.
    """
    with get_db() as db:
        migrated = db.execute(
            "SELECT 1 FROM schema_migrations WHERE version=?",
            (FSRS_RETENTION_098_MIGRATION,),
        ).fetchone()
        if migrated:
            return None

        backup_path = _backup_database_for_migration(db, FSRS_RETENTION_098_MIGRATION)
        db.execute("BEGIN IMMEDIATE")
        migrated = db.execute(
            "SELECT 1 FROM schema_migrations WHERE version=?",
            (FSRS_RETENTION_098_MIGRATION,),
        ).fetchone()
        if migrated:
            return backup_path

        stamp = now_iso()
        sentences = db.execute(
            """SELECT s.* FROM sentences s
               WHERE EXISTS(
                 SELECT 1 FROM review_events re WHERE re.sentence_id=s.id
               )
               ORDER BY s.id"""
        ).fetchall()
        for sentence in sentences:
            events = db.execute(
                """SELECT rating,reviewed_at,duration_ms
                   FROM review_events
                   WHERE sentence_id=?
                   ORDER BY reviewed_at,id""",
                (sentence["id"],),
            ).fetchall()
            after = reschedule_from_review_events(
                dict(sentence),
                events,
                enable_fuzzing=enable_fuzzing,
            )
            db.execute(
                """UPDATE sentences
                   SET fsrs_state=?,fsrs_step=?,stability=?,difficulty=?,
                       last_review_at=?,next_review_at=?,fsrs_version=?
                   WHERE id=?""",
                (
                    after["fsrs_state"], after["fsrs_step"], after["stability"],
                    after["difficulty"], after["last_review_at"],
                    after["next_review_at"], after["fsrs_version"], sentence["id"],
                ),
            )
        db.execute(
            "INSERT INTO schema_migrations(version,applied_at) VALUES(?,?)",
            (FSRS_RETENTION_098_MIGRATION, stamp),
        )
        return backup_path


def init_db(*, enable_fuzzing: bool = True) -> None:
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
        _migrate_unanswered_reports(db, stamp)
        _migrate_completion_mode(db, stamp)
        _migrate_automatic_rating(db, stamp)
        _migrate_chunk_schema(db)
        _migrate_sentence_note(db, stamp)
        _create_indexes(db)

        db.execute("DELETE FROM settings WHERE key IN ('scheduler_mode','base_url','model','custom_params','api_key_encrypted')")
        db.execute(
            "INSERT OR IGNORE INTO settings(key,value) VALUES('daily_auto_review_limit','50')"
        )
        db.execute(
            "INSERT OR IGNORE INTO collections(name,created_at,updated_at) VALUES('默认句集',?,?)",
            (stamp, stamp),
        )
    _migrate_no_short_steps(enable_fuzzing=enable_fuzzing)
    _migrate_fsrs_retention_098(enable_fuzzing=enable_fuzzing)


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
