from __future__ import annotations

import hashlib
import hmac
import json
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
    RATING_LABELS_ZH,
    RATING_NAMES,
    card_fields,
    new_card,
    rating_from_attempts,
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
from memory import is_valid_timezone, local_date, parse_iso
from security import hash_password, verify_password
from tokenizer import furigana_segments, local_tokenize, validate_chunks


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
    data["chunks"] = json_load(data.pop("chunks_json"), [])
    data["correctOrder"] = json_load(data.pop("correct_order_json"), [])
    data["furigana"] = json_load(data.pop("furigana_json", "[]"), [])
    return data


def sentence_snapshot(row):
    item = sentence_dict(row)
    snapshot = {key: item[key] for key in ("id", "chinese", "japanese", "chunks", "correctOrder", "furigana")}
    # Keep the report tied to the collection that produced it even if the
    # sentence is moved later. Older snapshots are resolved from live rows.
    snapshot["collectionId"] = item["collection_id"]
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


def _report_collection(db, item_rows):
    """Resolve a report's collection without consulting the UI selection.

    New attempts carry the original collection in their snapshot. For legacy
    attempts, fall back to the current collection of the report's surviving
    sentences. The most frequent collection wins, with report order breaking
    ties so mixed-selection reports stay deterministic.
    """
    snapshot_ids = []
    for row in item_rows:
        snapshot = json_load(row["sentence_snapshot_json"], {})
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
    return {"id": collection["id"], "name": collection["name"], "available": available}


def answers_match(answer, correct, chunks):
    """Compare answer order to correct order by chunk text, not by chunk id.

    Duplicate texts (e.g. two 「し」 with different ids) match when placed in the
    right positions even if the specific id instances are swapped.
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


def _finalize_question(db, session_id: int, sentence_id: int, *, easy: bool, enable_fuzzing: bool):
    """Finalize exactly one practice item inside the caller's transaction."""
    item = db.execute(
        "SELECT * FROM practice_items WHERE session_id=? AND sentence_id=?",
        (session_id, sentence_id),
    ).fetchone()
    if not item:
        return None, "练习或句子不存在"
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
        "SELECT * FROM attempts WHERE session_id=? AND sentence_id=? ORDER BY id",
        (session_id, sentence_id),
    ).fetchall()
    if not attempts:
        return None, "当前题还没有作答记录"
    rating = rating_from_attempts(attempts, easy=easy)
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
             sentence_id,session_id,rating,reviewed_at,duration_ms,is_new,
             fsrs_state_before,fsrs_state_after,fsrs_step_before,fsrs_step_after,
             stability_before,stability_after,difficulty_before,difficulty_after,
             next_review_before,next_review_after,fsrs_version,created_at
           ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            sentence_id, session_id, int(rating), after["last_review_at"], duration_ms,
            1 if before["last_review_at"] is None else 0,
            before["fsrs_state"], after["fsrs_state"], before["fsrs_step"], after["fsrs_step"],
            before["stability"], after["stability"], before["difficulty"], after["difficulty"],
            before["next_review_at"], after["next_review_at"], FSRS_VERSION, stamp,
        ),
    )
    db.execute(
        """UPDATE practice_items SET finalized_at=?,final_status=?,fsrs_rating=?,easy_selected=?
           WHERE session_id=? AND sentence_id=? AND finalized_at IS NULL""",
        (stamp, final_status, int(rating), int(rating is Rating.Easy), session_id, sentence_id),
    )
    return {
        "finalized": True,
        "status": final_status,
        "rating": RATING_NAMES[rating],
        "ratingLabel": RATING_LABELS_ZH[rating],
        "nextReviewAt": after["next_review_at"],
    }, None


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
        return jsonify(ok=True, time=int(time.time()), tokenizer="sudachipy-full-abc")

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
        now = now_iso()
        # 足够覆盖任意时区偏移(-12~+14)下的本地"今天"，把范围过滤下推到 SQL，
        # 避免全表扫描 review_events。
        lower_bound = (datetime.now(timezone.utc) - timedelta(days=2)).isoformat(timespec="seconds")
        with get_db() as db:
            tz = user_timezone(db)
            today = local_date(tz_name=tz)
            collections = [dict(row) for row in db.execute("""
              SELECT c.id,c.name,COUNT(s.id) total,
                SUM(CASE WHEN s.last_review_at IS NOT NULL THEN 1 ELSE 0 END) learned
              FROM collections c LEFT JOIN sentences s ON s.collection_id=c.id
              GROUP BY c.id ORDER BY c.created_at
            """)]
            # Align "today" with stats_learning: local calendar day + review_events
            # (result != skipped), distinct sentence_id per collection.
            event_rows = db.execute(
                """SELECT re.sentence_id, re.reviewed_at, s.collection_id
                   FROM review_events re
                   JOIN sentences s ON s.id = re.sentence_id
                   WHERE re.reviewed_at >= ?""",
                (lower_bound,),
            ).fetchall()
            today_ids: dict[int, set[int]] = {}
            for row in event_rows:
                dt = parse_iso(row["reviewed_at"])
                if not dt or local_date(dt, tz_name=tz) != today:
                    continue
                today_ids.setdefault(row["collection_id"], set()).add(row["sentence_id"])
            for item in collections:
                item["total"], item["learned"] = int(item["total"] or 0), int(item["learned"] or 0)
                item["due"] = db.execute("SELECT COUNT(*) n FROM sentences WHERE collection_id=? AND next_review_at<=?", (item["id"], now)).fetchone()["n"]
                item["today"] = len(today_ids.get(item["id"], set()))
        return jsonify(collections=collections)

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
        return jsonify(
            chunks=local_tokenize(japanese),
            source="sudachi",
            sentenceFurigana=furigana_segments(japanese),
        )

    def validate_sentence_payload(body):
        if not isinstance(body.get("chinese"), str) or not isinstance(body.get("japanese"), str):
            return None, "中文翻译和日语原句必须是字符串"
        chinese, japanese = body["chinese"].strip(), body["japanese"]
        chunks = body.get("chunks")
        if not chinese or not japanese.strip():
            return None, "中文翻译和日语原句都不能为空"
        valid, message = validate_chunks(japanese, chunks)
        if not valid:
            return None, message
        chunks = [{"id": item["id"], "text": item["text"]} for item in chunks]
        order = body.get("correctOrder") or [item["id"] for item in chunks]
        ids = [item["id"] for item in chunks]
        if order != ids:
            return None, "正确词块顺序必须与原句中的词块顺序一致"
        try:
            collection_id = int(body.get("collectionId"))
        except (ValueError, TypeError):
            return None, "请选择所属句集"
        return {"collection_id": collection_id, "chinese": chinese, "japanese": japanese, "chunks": chunks, "order": order}, ""

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
                    collection_id,chinese,japanese,chunks_json,correct_order_json,furigana_json,
                    fsrs_state,fsrs_step,stability,difficulty,last_review_at,next_review_at,
                    fsrs_version,created_at,updated_at
                  ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", (
                    item["collection_id"], item["chinese"], item["japanese"],
                    json.dumps(item["chunks"], ensure_ascii=False), json.dumps(item["order"]),
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
            changed = db.execute("""UPDATE sentences SET collection_id=?,chinese=?,japanese=?,chunks_json=?,correct_order_json=?,furigana_json=?,updated_at=? WHERE id=?""", (item["collection_id"], item["chinese"], item["japanese"], json.dumps(item["chunks"], ensure_ascii=False), json.dumps(item["order"]), furigana_json, now_iso(), sentence_id)).rowcount
        if changed:
            rebuild_fonts()
        return jsonify(ok=True) if changed else (jsonify(error="句子不存在"), 404)

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
        body = request.get_json(silent=True) or {}
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
                params, where = [now_iso()], "next_review_at<=?"
                if body.get("collectionId"):
                    where += " AND collection_id=?"; params.append(int(body["collectionId"]))
                requested = body.get("count")
                if requested in (None, "all"):
                    query, query_params = f"SELECT id FROM sentences WHERE {where} ORDER BY next_review_at,created_at", params
                else:
                    available = db.execute(f"SELECT COUNT(*) n FROM sentences WHERE {where}", params).fetchone()["n"]
                    subject = "当前句集待复习" if body.get("collectionId") else "当前待复习"
                    limit, msg = _resolve_limit(requested, available, subject)
                    if limit is None:
                        return jsonify(error=msg), 400
                    notice = msg
                    query, query_params = f"SELECT id FROM sentences WHERE {where} ORDER BY next_review_at,created_at LIMIT ?", [*params, limit]
                selected = [row["id"] for row in db.execute(query, query_params)]
                source = "due"
            if not selected:
                return jsonify(error="当前没有待复习句子"), 400
            cursor = db.execute("INSERT INTO practice_sessions(source,sentence_ids_json,total,created_at) VALUES(?,?,?,?)", (source, json.dumps(selected), len(selected), now_iso()))
            session_id = cursor.lastrowid
            db.executemany(
                "INSERT INTO practice_items(session_id,sentence_id,position) VALUES(?,?,?)",
                [(session_id, sentence_id, position) for position, sentence_id in enumerate(selected)],
            )
            rows = db.execute(f"SELECT * FROM sentences WHERE id IN ({','.join('?' for _ in selected)})", selected).fetchall()
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
        answer = body.get("answerOrder")
        if not isinstance(answer, list):
            answer = []
        try:
            duration_ms = max(0, int(body.get("durationMs") or 0))
        except (TypeError, ValueError):
            duration_ms = 0
        stamp = now_iso()
        with get_db() as db:
            practice = db.execute("SELECT * FROM practice_sessions WHERE id=?", (session_id,)).fetchone()
            row = db.execute("SELECT * FROM sentences WHERE id=?", (sentence_id,)).fetchone()
            item_row = db.execute(
                "SELECT finalized_at FROM practice_items WHERE session_id=? AND sentence_id=?",
                (session_id, sentence_id),
            ).fetchone()
            if not practice or not row or not item_row:
                return jsonify(error="练习或句子不存在"), 404
            if item_row["finalized_at"]:
                return jsonify(error="当前题已经结束"), 409
            item = sentence_dict(row)
            status = "skipped" if action == "skip" else ("correct" if answers_match(answer, item["correctOrder"], item["chunks"]) else "wrong")
            cursor = db.execute(
                """INSERT INTO attempts(
                     session_id,sentence_id,status,answer_order_json,sentence_snapshot_json,
                     duration_ms,created_at
                   ) VALUES(?,?,?,?,?,?,?)""",
                (
                    session_id, sentence_id, status, json.dumps(answer),
                    json.dumps(sentence_snapshot(row), ensure_ascii=False), duration_ms, stamp,
                ),
            )
        return jsonify(
            attemptId=cursor.lastrowid,
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
                easy=bool(body.get("easy")),
                enable_fuzzing=bool(app.config["FSRS_ENABLE_FUZZING"]),
            )
            if error:
                return jsonify(error=error), 404 if "不存在" in error else 409
        return jsonify(result)

    @app.post("/api/practice/sessions/<int:session_id>/complete")
    def complete_session(session_id):
        with get_db() as db:
            db.execute("BEGIN IMMEDIATE")
            practice = db.execute("SELECT total FROM practice_sessions WHERE id=?", (session_id,)).fetchone()
            if not practice:
                return jsonify(error="练习不存在"), 404
            pending = db.execute(
                """SELECT pi.sentence_id FROM practice_items pi
                   WHERE pi.session_id=? AND pi.finalized_at IS NULL
                     AND EXISTS(SELECT 1 FROM attempts a WHERE a.session_id=pi.session_id AND a.sentence_id=pi.sentence_id)""",
                (session_id,),
            ).fetchall()
            for pending_item in pending:
                _finalize_question(
                    db, session_id, pending_item["sentence_id"], easy=False,
                    enable_fuzzing=bool(app.config["FSRS_ENABLE_FUZZING"]),
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
                """UPDATE practice_sessions SET correct=?,wrong=?,skipped=?,
                   completed_at=COALESCE(completed_at,?) WHERE id=?""",
                (counts.get("correct", 0), counts.get("wrong", 0), counts.get("skipped", 0), now_iso(), session_id),
            )
        return jsonify(ok=True, reportId=session_id)

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
        return jsonify(reports=rows)

    @app.get("/api/reports/<int:session_id>")
    def report(session_id):
        with get_db() as db:
            practice = db.execute("SELECT * FROM practice_sessions WHERE id=?", (session_id,)).fetchone()
            items_rows = db.execute(
                """SELECT pi.*, a.answer_order_json, a.sentence_snapshot_json
                   FROM practice_items pi
                   LEFT JOIN attempts a ON a.id=(
                     SELECT a2.id FROM attempts a2
                     WHERE a2.session_id=pi.session_id AND a2.sentence_id=pi.sentence_id
                     ORDER BY a2.id DESC LIMIT 1
                   )
                   WHERE pi.session_id=? AND pi.finalized_at IS NOT NULL ORDER BY pi.position""",
                (session_id,),
            ).fetchall()
            if not practice or practice["report_deleted_at"]:
                return jsonify(error="报告不存在"), 404
            collection = _report_collection(db, items_rows)
        items = []
        for attempt in items_rows:
            snap = json_load(attempt["sentence_snapshot_json"], {})
            by_id = {chunk["id"]: chunk for chunk in snap.get("chunks", [])}
            answer = json_load(attempt["answer_order_json"], [])
            rating = Rating(attempt["fsrs_rating"]) if attempt["fsrs_rating"] else None
            items.append({
                "status": attempt["final_status"], "answerOrder": answer,
                "answerText": "".join(by_id.get(value, {}).get("text", "") for value in answer),
                "rating": RATING_NAMES.get(rating), "ratingLabel": RATING_LABELS_ZH.get(rating), **snap,
            })
        payload = dict(practice)
        payload["ratingCounts"] = _rating_counts(items_rows)
        payload["collection"] = collection
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

    # ---- FSRS statistics ----

    @app.get("/api/stats/summary")
    def stats_summary():
        now_dt = datetime.now(timezone.utc)
        now = now_dt.isoformat(timespec="seconds")
        horizons = {
            "days7": (now_dt + timedelta(days=7)).isoformat(timespec="seconds"),
            "days30": (now_dt + timedelta(days=30)).isoformat(timespec="seconds"),
            "days90": (now_dt + timedelta(days=90)).isoformat(timespec="seconds"),
        }
        lower_bound = (now_dt - timedelta(days=2)).isoformat(timespec="seconds")
        with get_db() as db:
            tz = user_timezone(db)
            events = [dict(row) for row in db.execute(
                """SELECT rating,reviewed_at,duration_ms,is_new FROM review_events
                   WHERE reviewed_at>=? ORDER BY reviewed_at""", (lower_bound,)
            )]
            sentences = [dict(row) for row in db.execute("SELECT * FROM sentences")]
            due_now = db.execute(
                "SELECT COUNT(*) n FROM sentences WHERE next_review_at<=?", (now,)
            ).fetchone()["n"]
            forecast = {
                key: db.execute(
                    """SELECT COUNT(*) n FROM sentences
                       WHERE next_review_at>? AND next_review_at<=?""", (now, cutoff)
                ).fetchone()["n"]
                for key, cutoff in horizons.items()
            }

        today = local_date(tz_name=tz)
        today_events = [
            event for event in events
            if (parsed := parse_iso(event["reviewed_at"]))
            and local_date(parsed, tz_name=tz) == today
        ]
        ratings = {name: 0 for name in ("again", "hard", "good", "easy")}
        for event in today_events:
            ratings[RATING_NAMES[Rating(event["rating"])]] += 1

        stability_bins = [
            {"label": "新卡", "min": None, "max": None, "count": 0},
            {"label": "<1 天", "min": 0, "max": 1, "count": 0},
            {"label": "1–7 天", "min": 1, "max": 7, "count": 0},
            {"label": "7–30 天", "min": 7, "max": 30, "count": 0},
            {"label": "30–90 天", "min": 30, "max": 90, "count": 0},
            {"label": "≥90 天", "min": 90, "max": None, "count": 0},
        ]
        difficulty_bins = [
            {"label": f"{start}–{start + 1}", "min": start, "max": start + 2, "count": 0}
            for start in range(1, 10, 2)
        ]
        reviewed = []
        for sentence in sentences:
            stability = sentence["stability"]
            if stability is None:
                stability_bins[0]["count"] += 1
            else:
                for bucket in stability_bins[1:]:
                    if stability >= bucket["min"] and (bucket["max"] is None or stability < bucket["max"]):
                        bucket["count"] += 1
                        break
                reviewed.append(sentence)
            difficulty = sentence["difficulty"]
            if difficulty is not None:
                index = min(4, max(0, int((float(difficulty) - 1) // 2)))
                difficulty_bins[index]["count"] += 1

        retention_pct = round(
            sum(retrievability(sentence, now_dt) for sentence in reviewed) * 100 / len(reviewed), 1
        ) if reviewed else None
        return jsonify(
            fsrs={
                "version": FSRS_VERSION,
                "desiredRetention": DESIRED_RETENTION,
                "maximumIntervalDays": MAXIMUM_INTERVAL_DAYS,
            },
            today={
                "learned": sum(int(event["is_new"] or 0) for event in today_events),
                "reviewed": sum(not int(event["is_new"] or 0) for event in today_events),
                "ratings": ratings,
                "durationSec": round(sum(int(event["duration_ms"] or 0) for event in today_events) / 1000),
            },
            dueNow=due_now,
            forecast=forecast,
            stabilityDistribution=stability_bins,
            difficultyDistribution=difficulty_bins,
            retentionPct=retention_pct,
            reviewedCards=len(reviewed),
        )

    # Build content-subset fonts at startup (no-op if sources missing / already current).
    if not app.config.get("TESTING"):
        try:
            ensure_active_fonts()
        except Exception:
            app.logger.exception("Initial active font build failed")

    return app


app = create_app()
