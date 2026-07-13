from __future__ import annotations

import time
from flask import request, session
from db import setting

MAX_FAILURES = 5
LOCK_SECONDS = 15 * 60


def configured(db):
    return bool(setting(db, "auth_username") and setting(db, "auth_password_hash"))


def authed():
    created = session.get("authed_at")
    return bool(created and time.time() - created < 7 * 86400)


def keys(username):
    ip = (request.remote_addr or "unknown").strip()
    user = (username or "").strip().casefold()
    return [f"ip:{ip}"] + ([f"user:{user}"] if user else [])


def lock_remaining(db, identifiers):
    now = time.time()
    values = []
    for identifier in identifiers:
        row = db.execute("SELECT locked_until FROM login_attempts WHERE identifier=?", (identifier,)).fetchone()
        values.append(max(0, float(row["locked_until"] or 0) - now) if row else 0)
    return max(values, default=0)


def fail(db, identifiers):
    now = time.time()
    for identifier in identifiers:
        db.execute("""
          INSERT INTO login_attempts(identifier,fail_count,last_failed_at,locked_until) VALUES(?,1,?,0)
          ON CONFLICT(identifier) DO UPDATE SET
            fail_count=CASE WHEN locked_until>0 AND locked_until<=excluded.last_failed_at THEN 1 ELSE fail_count+1 END,
            last_failed_at=excluded.last_failed_at,
            locked_until=CASE WHEN (CASE WHEN locked_until>0 AND locked_until<=excluded.last_failed_at THEN 1 ELSE fail_count+1 END)>=? THEN excluded.last_failed_at+? ELSE 0 END
        """, (identifier, now, MAX_FAILURES, LOCK_SECONDS))


def clear(db, identifiers):
    db.executemany("DELETE FROM login_attempts WHERE identifier=?", [(x,) for x in identifiers])

