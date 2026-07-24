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
SENTENCE_NOTE_MIGRATION = "sentence_note_v1"
PRACTICE_CARDS_MIGRATION = "practice_cards_kwja_v1"
KWJA_ANALYSIS_MIGRATION = "kwja_tiny_analysis_v1"
LEGACY_SUFFIX = "_legacy_kwja_v1"


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


def _create_practice_cards_table(db) -> None:
    db.execute("""CREATE TABLE IF NOT EXISTS practice_cards (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        sentence_id INTEGER NOT NULL REFERENCES sentences(id) ON DELETE CASCADE,
        card_type TEXT NOT NULL
            CHECK(card_type IN ('sentence_order','kanji_reading')),
        card_key TEXT NOT NULL,
        payload_json TEXT NOT NULL DEFAULT '{}',
        active INTEGER NOT NULL DEFAULT 1 CHECK(active IN (0,1)),
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
    db.execute(
        """CREATE UNIQUE INDEX IF NOT EXISTS ux_practice_cards_active_key
           ON practice_cards(sentence_id,card_type,card_key) WHERE active=1"""
    )
    db.execute(
        """CREATE UNIQUE INDEX IF NOT EXISTS ux_practice_cards_active_order
           ON practice_cards(sentence_id)
           WHERE card_type='sentence_order' AND active=1"""
    )
    db.execute(
        """CREATE INDEX IF NOT EXISTS idx_practice_cards_schedule
           ON practice_cards(card_type,active,next_review_at)"""
    )
    db.execute(
        """CREATE INDEX IF NOT EXISTS idx_practice_cards_sentence
           ON practice_cards(sentence_id,card_type,active)"""
    )


def _create_card_review_tables(db) -> None:
    db.execute("""CREATE TABLE IF NOT EXISTS practice_items (
        session_id INTEGER NOT NULL REFERENCES practice_sessions(id) ON DELETE CASCADE,
        card_id INTEGER NOT NULL REFERENCES practice_cards(id) ON DELETE RESTRICT,
        sentence_id INTEGER NOT NULL REFERENCES sentences(id) ON DELETE CASCADE,
        position INTEGER NOT NULL,
        finalized_at TEXT,
        unanswered_at TEXT,
        final_status TEXT CHECK(final_status IN ('correct','wrong','skipped')),
        fsrs_rating INTEGER CHECK(fsrs_rating BETWEEN 1 AND 4),
        easy_selected INTEGER NOT NULL DEFAULT 0 CHECK(easy_selected IN (0,1)),
        draft_answer_json TEXT NOT NULL DEFAULT '{}',
        draft_answer_order_json TEXT NOT NULL DEFAULT '[]',
        card_snapshot_json TEXT,
        sentence_snapshot_json TEXT,
        PRIMARY KEY(session_id, card_id),
        UNIQUE(session_id, position)
    )""")
    db.execute("""CREATE TABLE IF NOT EXISTS attempts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        session_id INTEGER NOT NULL REFERENCES practice_sessions(id) ON DELETE CASCADE,
        card_id INTEGER NOT NULL REFERENCES practice_cards(id) ON DELETE RESTRICT,
        sentence_id INTEGER NOT NULL REFERENCES sentences(id) ON DELETE CASCADE,
        client_attempt_id TEXT,
        attempt_number INTEGER NOT NULL CHECK(attempt_number > 0),
        status TEXT NOT NULL CHECK(status IN ('correct','wrong','skipped')),
        answer_json TEXT NOT NULL DEFAULT '{}',
        answer_order_json TEXT NOT NULL DEFAULT '[]',
        card_snapshot_json TEXT,
        sentence_snapshot_json TEXT NOT NULL,
        duration_ms INTEGER NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL
    )""")
    db.execute("""CREATE TABLE IF NOT EXISTS review_events (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        card_id INTEGER NOT NULL REFERENCES practice_cards(id) ON DELETE RESTRICT,
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
        UNIQUE(session_id, card_id)
    )""")


def _create_kwja_migration_tables(db) -> None:
    db.execute("""CREATE TABLE IF NOT EXISTS kwja_migration_items (
        sentence_id INTEGER PRIMARY KEY REFERENCES sentences(id) ON DELETE CASCADE,
        status TEXT NOT NULL DEFAULT 'pending'
            CHECK(status IN ('pending','processing','success','failed')),
        input_sha256 TEXT NOT NULL,
        old_analysis_source TEXT,
        old_analysis_version TEXT,
        old_chunks_json TEXT,
        old_correct_order_json TEXT,
        old_practice_structure_json TEXT,
        old_furigana_json TEXT,
        old_chunks_manually_edited INTEGER NOT NULL DEFAULT 0,
        new_analysis_source TEXT,
        new_analysis_version TEXT,
        phrase_count INTEGER,
        reading_card_count INTEGER,
        reading_skip_count INTEGER,
        reading_skip_reasons_json TEXT NOT NULL DEFAULT '{}',
        error_message TEXT,
        started_at TEXT,
        completed_at TEXT,
        updated_at TEXT NOT NULL
    )""")
    db.execute(
        """CREATE INDEX IF NOT EXISTS idx_kwja_migration_status
           ON kwja_migration_items(status,sentence_id)"""
    )


def _create_indexes(db) -> None:
    if _table_exists(db, "attempts") and "card_id" in _columns(db, "attempts"):
        _create_card_indexes(db)
        return
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


def _create_card_indexes(db) -> None:
    statements = (
        "CREATE INDEX IF NOT EXISTS idx_attempts_session_card ON attempts(session_id,card_id,id)",
        "CREATE INDEX IF NOT EXISTS idx_attempts_card ON attempts(card_id)",
        "CREATE UNIQUE INDEX IF NOT EXISTS ux_attempts_client_id ON attempts(client_attempt_id) WHERE client_attempt_id IS NOT NULL",
        "CREATE UNIQUE INDEX IF NOT EXISTS ux_attempts_number ON attempts(session_id,card_id,attempt_number)",
        "CREATE INDEX IF NOT EXISTS idx_sessions_created ON practice_sessions(created_at DESC)",
        "CREATE INDEX IF NOT EXISTS idx_review_events_reviewed ON review_events(reviewed_at)",
        "CREATE INDEX IF NOT EXISTS idx_review_events_card ON review_events(card_id,reviewed_at)",
        "CREATE INDEX IF NOT EXISTS idx_review_events_sentence ON review_events(sentence_id,reviewed_at)",
        "CREATE INDEX IF NOT EXISTS idx_practice_items_unanswered ON practice_items(session_id,unanswered_at)",
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
    """Add derived practice-analysis fields without rewriting sentence data."""
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

    sentence_columns = _columns(db, "sentences")
    analysis_additions = (
        ("analysis_json", "TEXT"),
        ("analysis_input_sha256", "TEXT"),
        ("analysis_version", "TEXT"),
        ("analysis_updated_at", "TEXT"),
    )
    for column, definition in analysis_additions:
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
    attempt_key = "card_id" if "card_id" in attempt_columns else "sentence_id"
    db.execute(
        f"""UPDATE attempts AS target
            SET attempt_number=(
              SELECT COUNT(*) FROM attempts AS preceding
              WHERE preceding.session_id=target.session_id
                AND preceding.{attempt_key}=target.{attempt_key}
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
    event_key = "card_id" if "card_id" in event_columns else "sentence_id"
    legacy_events = db.execute(
        f"""SELECT id,session_id,{event_key} AS item_key FROM review_events
           WHERE attempt_count=0 AND first_attempt_correct IS NULL"""
    ).fetchall()
    for event in legacy_events:
        attempts = db.execute(
            f"""SELECT status FROM attempts
               WHERE session_id=? AND {attempt_key}=?
               ORDER BY attempt_number,id""",
            (event["session_id"], event["item_key"]),
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


def _add_card_session_columns(db) -> None:
    columns = _columns(db, "practice_sessions")
    if "card_ids_json" not in columns:
        db.execute(
            "ALTER TABLE practice_sessions ADD COLUMN "
            "card_ids_json TEXT NOT NULL DEFAULT '[]'"
        )
    if "card_type" not in columns:
        db.execute(
            "ALTER TABLE practice_sessions ADD COLUMN "
            "card_type TEXT NOT NULL DEFAULT 'sentence_order'"
        )


def _migrate_practice_cards(db, stamp: str) -> None:
    """Copy sentence scheduling/history to card-owned, general practice tables."""
    migrated = db.execute(
        "SELECT 1 FROM schema_migrations WHERE version=?",
        (PRACTICE_CARDS_MIGRATION,),
    ).fetchone()
    if migrated:
        _create_practice_cards_table(db)
        _add_card_session_columns(db)
        _create_kwja_migration_tables(db)
        _create_card_indexes(db)
        return

    _create_practice_cards_table(db)
    _add_card_session_columns(db)
    db.execute(
        """INSERT INTO practice_cards(
             sentence_id,card_type,card_key,payload_json,active,
             fsrs_state,fsrs_step,stability,difficulty,last_review_at,
             next_review_at,fsrs_version,created_at,updated_at
           )
           SELECT s.id,'sentence_order','sentence_order','{"schemaVersion":1}',1,
                  s.fsrs_state,s.fsrs_step,s.stability,s.difficulty,
                  s.last_review_at,s.next_review_at,s.fsrs_version,
                  s.created_at,s.updated_at
           FROM sentences s
           WHERE NOT EXISTS(
             SELECT 1 FROM practice_cards pc
             WHERE pc.sentence_id=s.id
               AND pc.card_type='sentence_order'
               AND pc.active=1
           )"""
    )

    fsrs_mismatch = db.execute(
        """SELECT COUNT(*) FROM sentences s
           JOIN practice_cards pc
             ON pc.sentence_id=s.id
            AND pc.card_type='sentence_order'
            AND pc.active=1
           WHERE pc.fsrs_state IS NOT s.fsrs_state
              OR pc.fsrs_step IS NOT s.fsrs_step
              OR pc.stability IS NOT s.stability
              OR pc.difficulty IS NOT s.difficulty
              OR pc.last_review_at IS NOT s.last_review_at
              OR pc.next_review_at IS NOT s.next_review_at
              OR pc.fsrs_version IS NOT s.fsrs_version"""
    ).fetchone()[0]
    if fsrs_mismatch:
        raise sqlite3.IntegrityError("sentence_order card FSRS copy verification failed")

    if "card_id" not in _columns(db, "practice_items"):
        legacy_names = {
            "practice_items": f"practice_items{LEGACY_SUFFIX}",
            "attempts": f"attempts{LEGACY_SUFFIX}",
            "review_events": f"review_events{LEGACY_SUFFIX}",
        }
        for legacy in legacy_names.values():
            if _table_exists(db, legacy):
                raise sqlite3.IntegrityError(
                    f"legacy table {legacy} already exists without migration marker"
                )

        old_counts = {
            table: db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in legacy_names
        }
        for index in (
            "idx_attempts_session_sentence",
            "idx_attempts_sentence",
            "ux_attempts_client_id",
            "ux_attempts_number",
            "idx_review_events_reviewed",
            "idx_review_events_sentence",
            "idx_practice_items_unanswered",
        ):
            db.execute(f"DROP INDEX IF EXISTS {index}")
        for current, legacy in legacy_names.items():
            db.execute(f"ALTER TABLE {current} RENAME TO {legacy}")

        _create_card_review_tables(db)
        items_legacy = legacy_names["practice_items"]
        attempts_legacy = legacy_names["attempts"]
        events_legacy = legacy_names["review_events"]

        db.execute(
            f"""INSERT INTO practice_items(
                  session_id,card_id,sentence_id,position,finalized_at,
                  unanswered_at,final_status,fsrs_rating,easy_selected,
                  draft_answer_json,draft_answer_order_json,
                  card_snapshot_json,sentence_snapshot_json
                )
                SELECT old.session_id,pc.id,old.sentence_id,old.position,
                       old.finalized_at,old.unanswered_at,old.final_status,
                       old.fsrs_rating,old.easy_selected,
                       json_object(
                         'type','sentence_order',
                         'orderedChunkIds',json(old.draft_answer_order_json)
                       ),
                       old.draft_answer_order_json,
                       json_object('type','sentence_order','cardKey','sentence_order'),
                       old.sentence_snapshot_json
                FROM {items_legacy} old
                JOIN practice_cards pc
                  ON pc.sentence_id=old.sentence_id
                 AND pc.card_type='sentence_order'
                 AND pc.active=1
                ORDER BY old.session_id,old.position"""
        )
        db.execute(
            f"""INSERT INTO attempts(
                  id,session_id,card_id,sentence_id,client_attempt_id,
                  attempt_number,status,answer_json,answer_order_json,
                  card_snapshot_json,sentence_snapshot_json,duration_ms,created_at
                )
                SELECT old.id,old.session_id,pc.id,old.sentence_id,
                       old.client_attempt_id,old.attempt_number,old.status,
                       json_object(
                         'type','sentence_order',
                         'orderedChunkIds',json(old.answer_order_json)
                       ),
                       old.answer_order_json,
                       json_object('type','sentence_order','cardKey','sentence_order'),
                       old.sentence_snapshot_json,old.duration_ms,old.created_at
                FROM {attempts_legacy} old
                JOIN practice_cards pc
                  ON pc.sentence_id=old.sentence_id
                 AND pc.card_type='sentence_order'
                 AND pc.active=1
                ORDER BY old.id"""
        )
        db.execute(
            f"""INSERT INTO review_events(
                  id,card_id,sentence_id,session_id,rating,attempt_count,
                  first_attempt_correct,second_attempt_correct,
                  final_attempt_correct,rating_policy_version,reviewed_at,
                  duration_ms,is_new,fsrs_state_before,fsrs_state_after,
                  fsrs_step_before,fsrs_step_after,stability_before,
                  stability_after,difficulty_before,difficulty_after,
                  next_review_before,next_review_after,fsrs_version,created_at
                )
                SELECT old.id,pc.id,old.sentence_id,old.session_id,old.rating,
                       old.attempt_count,old.first_attempt_correct,
                       old.second_attempt_correct,old.final_attempt_correct,
                       old.rating_policy_version,old.reviewed_at,
                       old.duration_ms,old.is_new,old.fsrs_state_before,
                       old.fsrs_state_after,old.fsrs_step_before,
                       old.fsrs_step_after,old.stability_before,
                       old.stability_after,old.difficulty_before,
                       old.difficulty_after,old.next_review_before,
                       old.next_review_after,old.fsrs_version,old.created_at
                FROM {events_legacy} old
                JOIN practice_cards pc
                  ON pc.sentence_id=old.sentence_id
                 AND pc.card_type='sentence_order'
                 AND pc.active=1
                ORDER BY old.id"""
        )
        for table, old_count in old_counts.items():
            new_count = db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            if new_count != old_count:
                raise sqlite3.IntegrityError(
                    f"{table} history count changed: {old_count} -> {new_count}"
                )

    sessions = db.execute("SELECT id FROM practice_sessions ORDER BY id").fetchall()
    for session in sessions:
        card_ids = [
            row["card_id"]
            for row in db.execute(
                """SELECT card_id FROM practice_items
                   WHERE session_id=? ORDER BY position""",
                (session["id"],),
            ).fetchall()
        ]
        db.execute(
            """UPDATE practice_sessions
               SET card_ids_json=?,card_type='sentence_order'
               WHERE id=?""",
            (json.dumps(card_ids, ensure_ascii=False), session["id"]),
        )

    _create_kwja_migration_tables(db)
    _create_card_indexes(db)
    foreign_key_errors = db.execute("PRAGMA foreign_key_check").fetchall()
    if foreign_key_errors:
        raise sqlite3.IntegrityError(
            f"practice-card migration produced {len(foreign_key_errors)} foreign-key errors"
        )
    db.execute(
        "INSERT INTO schema_migrations(version,applied_at) VALUES(?,?)",
        (PRACTICE_CARDS_MIGRATION, stamp),
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
            "INSERT OR IGNORE INTO settings(key,value) "
            "VALUES('daily_kanji_reading_review_limit','30')"
        )
        db.execute(
            "INSERT OR IGNORE INTO collections(name,created_at,updated_at) VALUES('默认句集',?,?)",
            (stamp, stamp),
        )
    _migrate_no_short_steps(enable_fuzzing=enable_fuzzing)
    _migrate_fsrs_retention_098(enable_fuzzing=enable_fuzzing)
    with get_db() as db:
        db.execute("BEGIN IMMEDIATE")
        stamp = now_iso()
        _migrate_practice_cards(db, stamp)
        db.execute(
            "INSERT OR IGNORE INTO settings(key,value) "
            "VALUES('daily_kanji_reading_review_limit','30')"
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
