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
from font_active import (
    active_dir,
    ensure_active_fonts,
    faces_css_text,
    safe_font_filename,
    schedule_font_rebuild,
    status as font_status,
)
from memory import (
    COGNITIVE_RESULTS,
    DEFAULT_SCHEDULER_MODE,
    DUE_PRESSURE_THRESHOLD,
    HOLD_THRESHOLDS,
    INITIAL_S,
    MIN_CURVE_SAMPLES,
    SUCCESS_RESULTS,
    FIXED_INTERVALS as INTERVALS,
    blend_user_rate,
    grade_attempt,
    hold_days,
    local_date,
    parse_iso,
    schedule_next,
    theory_curve_points,
    update_stability,
)
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
    """级联硬删除给定句子及其 review_events / attempts 记录。空列表直接返回。"""
    if not sentence_ids:
        return
    placeholders = ",".join("?" for _ in sentence_ids)
    db.execute(f"DELETE FROM review_events WHERE sentence_id IN ({placeholders})", sentence_ids)
    db.execute(f"DELETE FROM attempts WHERE sentence_id IN ({placeholders})", sentence_ids)
    db.execute(f"DELETE FROM sentences WHERE id IN ({placeholders})", sentence_ids)


def sentence_dict(row):
    data = dict(row)
    data["chunks"] = json_load(data.pop("chunks_json"), [])
    data["correctOrder"] = json_load(data.pop("correct_order_json"), [])
    data["furigana"] = json_load(data.pop("furigana_json", "[]"), [])
    return data


def sentence_snapshot(row):
    item = sentence_dict(row)
    return {key: item[key] for key in ("id", "chinese", "japanese", "chunks", "correctOrder", "furigana")}


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


def stats_snapshot(row):
    keys = (
        "study_count", "correct_count", "wrong_count", "skip_count", "correct_streak",
        "next_review_at", "last_practiced_at", "stability", "review_count", "lapse_count",
    )
    data = dict(row)
    return {
        key: data.get(key, INITIAL_S if key == "stability" else (0 if key in ("review_count", "lapse_count") else None))
        for key in keys
    }


def scheduler_mode(db) -> str:
    mode = (setting(db, "scheduler_mode", DEFAULT_SCHEDULER_MODE) or DEFAULT_SCHEDULER_MODE).lower()
    return mode if mode in {"dynamic", "fixed"} else DEFAULT_SCHEDULER_MODE


def apply_attempt_stats(
    db,
    row,
    sentence_id,
    status,
    stamp,
    base=None,
    *,
    session_id=None,
    attempt_n=1,
    duration_ms=0,
    force_fuzzy=False,
):
    """Update sentence SRS fields and upsert a review_events row for this attempt."""
    base = base or stats_snapshot(row)
    study = int(base["study_count"] or 0) + (status != "skipped")
    correct = int(base["correct_count"] or 0) + (status == "correct")
    wrong = int(base["wrong_count"] or 0) + (status == "wrong")
    skipped = int(base["skip_count"] or 0) + (status == "skipped")
    streak = int(base["correct_streak"] or 0)
    review_count = int(base.get("review_count") or 0)
    lapse_count = int(base.get("lapse_count") or 0)
    stability_before = float(base.get("stability") if base.get("stability") is not None else INITIAL_S)
    is_new = 1 if int(base["study_count"] or 0) == 0 and status != "skipped" else 0

    result = grade_attempt(
        status, attempt_n=attempt_n, duration_ms=duration_ms, force_fuzzy=force_fuzzy,
    )
    due = base["next_review_at"]
    interval = 0.0
    stability_after = stability_before
    mode = scheduler_mode(db)

    if status == "correct":
        streak += 1
        stability_after = update_stability(stability_before, result)
        review_count += 1
        due, interval = schedule_next(
            mode=mode, result=result, stability_after=stability_after, streak_after=streak,
        )
    elif status == "wrong":
        streak = 0
        stability_after = update_stability(stability_before, "forgotten")
        review_count += 1
        lapse_count += 1
        due, interval = schedule_next(
            mode=mode, result="forgotten", stability_after=stability_after, streak_after=streak,
        )
    else:
        # skipped: keep due and stability
        stability_after = stability_before
        due = base["next_review_at"]
        interval = 0.0

    db.execute(
        """UPDATE sentences SET study_count=?,correct_count=?,wrong_count=?,skip_count=?,correct_streak=?,
           stability=?,review_count=?,lapse_count=?,next_review_at=?,last_practiced_at=?,updated_at=? WHERE id=?""",
        (
            study, correct, wrong, skipped, streak,
            stability_after, review_count, lapse_count, due, stamp, stamp, sentence_id,
        ),
    )

    # Upsert one review_event per (session, sentence) so retries don't double-count
    if session_id is not None:
        existing = db.execute(
            "SELECT id FROM review_events WHERE session_id=? AND sentence_id=? ORDER BY id LIMIT 1",
            (session_id, sentence_id),
        ).fetchone()
        if existing:
            db.execute(
                """UPDATE review_events SET reviewed_at=?, result=?, duration_ms=?, attempt_n=?,
                   is_new=?, stability_before=?, stability_after=?, interval_days=?, created_at=? WHERE id=?""",
                (
                    stamp, result, int(duration_ms or 0), int(attempt_n or 1),
                    is_new, stability_before, stability_after, interval, stamp, existing["id"],
                ),
            )
        else:
            db.execute(
                """INSERT INTO review_events(
                     sentence_id, session_id, reviewed_at, result, duration_ms, attempt_n,
                     is_new, stability_before, stability_after, interval_days, created_at
                   ) VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    sentence_id, session_id, stamp, result, int(duration_ms or 0), int(attempt_n or 1),
                    is_new, stability_before, stability_after, interval, stamp,
                ),
            )
    return result


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
    )
    if test_config:
        app.config.update(test_config)
    count = int(os.environ.get("TRUST_PROXY_COUNT", "1") or 0)
    if count:
        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=count, x_proto=count, x_host=count)

    init_db()
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
        today = local_date()
        now = now_iso()
        # 足够覆盖任意时区偏移(-12~+14)下的本地"今天"，把范围过滤下推到 SQL，
        # 避免全表扫描 review_events。
        lower_bound = (datetime.now(timezone.utc) - timedelta(days=2)).isoformat(timespec="seconds")
        with get_db() as db:
            collections = [dict(row) for row in db.execute("""
              SELECT c.id,c.name,COUNT(s.id) total,
                SUM(CASE WHEN s.study_count>0 THEN 1 ELSE 0 END) learned
              FROM collections c LEFT JOIN sentences s ON s.collection_id=c.id
              GROUP BY c.id ORDER BY c.created_at
            """)]
            # Align "today" with stats_learning: local calendar day + review_events
            # (result != skipped), distinct sentence_id per collection.
            event_rows = db.execute(
                """SELECT re.sentence_id, re.reviewed_at, s.collection_id
                   FROM review_events re
                   JOIN sentences s ON s.id = re.sentence_id
                   WHERE re.result != 'skipped' AND re.sentence_id IS NOT NULL
                     AND re.reviewed_at >= ?""",
                (lower_bound,),
            ).fetchall()
            today_ids: dict[int, set[int]] = {}
            for row in event_rows:
                dt = parse_iso(row["reviewed_at"])
                if not dt or local_date(dt) != today:
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
            schedule_font_rebuild()
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
            schedule_font_rebuild()
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
        schedule_font_rebuild()
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
                cursor = db.execute("""INSERT INTO sentences(collection_id,chinese,japanese,chunks_json,correct_order_json,furigana_json,next_review_at,created_at,updated_at)
                  VALUES(?,?,?,?,?,?,?,?,?)""", (item["collection_id"], item["chinese"], item["japanese"], json.dumps(item["chunks"], ensure_ascii=False), json.dumps(item["order"]), furigana_json, stamp, stamp, stamp))
                row = db.execute("SELECT * FROM sentences WHERE id=?", (cursor.lastrowid,)).fetchone()
            schedule_font_rebuild()
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
        sort = {"created":"s.created_at DESC", "recent":"COALESCE(s.last_practiced_at,'') DESC", "error":"CASE WHEN s.study_count=0 THEN 0.0 ELSE CAST(s.wrong_count AS REAL)/s.study_count END DESC"}.get(request.args.get("sort"), "s.created_at DESC")
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
            schedule_font_rebuild()
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
            schedule_font_rebuild()
        return jsonify(ok=True, moved=moved)

    @app.delete("/api/sentences/<int:sentence_id>")
    def delete_sentence(sentence_id):
        # Hard-delete related stats/history first (before FK SET NULL would orphan them).
        with get_db() as db:
            exists = db.execute("SELECT id FROM sentences WHERE id=?", (sentence_id,)).fetchone()
            if not exists:
                return jsonify(error="句子不存在"), 404
            _hard_delete_sentences(db, [sentence_id])
        schedule_font_rebuild()
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
                # retryWrong: report-page "练习本轮错题" — grade first correct as fuzzy
                source = "retry_wrong" if body.get("retryWrong") else "selected"
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
            rows = db.execute(f"SELECT * FROM sentences WHERE id IN ({','.join('?' for _ in selected)})", selected).fetchall()
            mapped = {row["id"]: sentence_dict(row) for row in rows}
        return jsonify(sessionId=cursor.lastrowid, sentences=[mapped[x] for x in selected], notice=notice), 201

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
            if not practice or not row or sentence_id not in json_load(practice["sentence_ids_json"], []):
                return jsonify(error="练习或句子不存在"), 404
            item = sentence_dict(row)
            status = "skipped" if action == "skip" else ("correct" if answers_match(answer, item["correctOrder"], item["chunks"]) else "wrong")
            force_fuzzy = practice["source"] == "retry_wrong"
            previous = db.execute("SELECT * FROM attempts WHERE session_id=? AND sentence_id=? ORDER BY id LIMIT 1", (session_id, sentence_id)).fetchone()
            if previous:
                base = json_load(previous["stats_before_json"], None) or stats_snapshot(row)
                prev = dict(previous)
                attempt_n = int(prev.get("attempt_n") or 0) + 1
                grade = grade_attempt(
                    status, attempt_n=attempt_n, duration_ms=duration_ms, force_fuzzy=force_fuzzy,
                )
                db.execute(
                    """UPDATE attempts SET status=?,answer_order_json=?,sentence_snapshot_json=?,created_at=?,
                       duration_ms=?,attempt_n=?,grade=? WHERE id=?""",
                    (
                        status, json.dumps(answer), json.dumps(sentence_snapshot(row), ensure_ascii=False), stamp,
                        duration_ms, attempt_n, grade, previous["id"],
                    ),
                )
            else:
                base = stats_snapshot(row)
                attempt_n = 1
                grade = grade_attempt(
                    status, attempt_n=attempt_n, duration_ms=duration_ms, force_fuzzy=force_fuzzy,
                )
                db.execute(
                    """INSERT INTO attempts(
                         session_id,sentence_id,status,answer_order_json,sentence_snapshot_json,
                         stats_before_json,duration_ms,attempt_n,grade,created_at
                       ) VALUES(?,?,?,?,?,?,?,?,?,?)""",
                    (
                        session_id, sentence_id, status, json.dumps(answer),
                        json.dumps(sentence_snapshot(row), ensure_ascii=False), json.dumps(base),
                        duration_ms, attempt_n, grade, stamp,
                    ),
                )
            grade = apply_attempt_stats(
                db, row, sentence_id, status, stamp, base,
                session_id=session_id, attempt_n=attempt_n, duration_ms=duration_ms,
                force_fuzzy=force_fuzzy,
            )
        return jsonify(
            status=status,
            correctOrder=item["correctOrder"],
            correct=status == "correct",
            grade=grade,
        )

    @app.post("/api/practice/sessions/<int:session_id>/complete")
    def complete_session(session_id):
        with get_db() as db:
            counts = {row["status"]: row["n"] for row in db.execute("SELECT status,COUNT(*) n FROM attempts WHERE session_id=? GROUP BY status", (session_id,))}
            practice = db.execute("SELECT total FROM practice_sessions WHERE id=?", (session_id,)).fetchone()
            if not practice:
                return jsonify(error="练习不存在"), 404
            db.execute("UPDATE practice_sessions SET correct=?,wrong=?,skipped=?,completed_at=? WHERE id=?", (counts.get("correct", 0), counts.get("wrong", 0), counts.get("skipped", 0), now_iso(), session_id))
        return jsonify(ok=True, reportId=session_id)

    @app.get("/api/reports")
    def reports():
        with get_db() as db:
            rows = [dict(row) for row in db.execute("SELECT * FROM practice_sessions WHERE completed_at IS NOT NULL ORDER BY created_at DESC LIMIT 100")]
        for row in rows:
            row["accuracy"] = round(row["correct"] * 100 / row["total"], 1) if row["total"] else 0
        return jsonify(reports=rows)

    @app.get("/api/reports/<int:session_id>")
    def report(session_id):
        with get_db() as db:
            practice = db.execute("SELECT * FROM practice_sessions WHERE id=?", (session_id,)).fetchone()
            attempts = db.execute("SELECT * FROM attempts WHERE session_id=? ORDER BY id", (session_id,)).fetchall()
        if not practice:
            return jsonify(error="报告不存在"), 404
        items = []
        for attempt in attempts:
            snap = json_load(attempt["sentence_snapshot_json"], {})
            by_id = {chunk["id"]: chunk for chunk in snap.get("chunks", [])}
            answer = json_load(attempt["answer_order_json"], [])
            items.append({"status": attempt["status"], "answerOrder": answer, "answerText": "".join(by_id.get(value, {}).get("text", "") for value in answer), **snap})
        payload = dict(practice)
        payload["accuracy"] = round(payload["correct"] * 100 / payload["total"], 1) if payload["total"] else 0
        payload["items"] = items
        return jsonify(report=payload)

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

    @app.get("/api/settings/scheduler")
    def get_scheduler_settings():
        with get_db() as db:
            mode = scheduler_mode(db)
        return jsonify(mode=mode, intervals=list(INTERVALS), defaultMode=DEFAULT_SCHEDULER_MODE)

    @app.put("/api/settings/scheduler")
    def save_scheduler_settings():
        body = request.get_json(silent=True) or {}
        mode = str(body.get("mode", "")).strip().lower()
        if mode not in {"dynamic", "fixed"}:
            return jsonify(error="mode 必须是 dynamic 或 fixed"), 400
        with get_db() as db:
            set_setting(db, "scheduler_mode", mode)
        return jsonify(ok=True, mode=mode)

    # ---- Stats APIs ----

    def _bucket_specs(granularity: str):
        """Return (granularity, list of (label, start_date, end_date exclusive))."""
        today = local_date()
        g = (granularity or "day").lower()
        if g not in {"day", "week", "month"}:
            g = "day"
        buckets = []
        if g == "day":
            for i in range(89, -1, -1):
                d = today - timedelta(days=i)
                label = "今天" if i == 0 else f"{i}天前"
                buckets.append((label, d, d + timedelta(days=1)))
        elif g == "week":
            # Monday-based weeks; 26 weeks ending this week
            weekday = today.weekday()  # Mon=0
            this_week_start = today - timedelta(days=weekday)
            for i in range(25, -1, -1):
                start = this_week_start - timedelta(weeks=i)
                end = start + timedelta(weeks=1)
                label = "本周" if i == 0 else f"{i}周前"
                buckets.append((label, start, end))
        else:
            # 12 calendar months ending current month
            y, m = today.year, today.month
            for i in range(11, -1, -1):
                mm = m - i
                yy = y
                while mm <= 0:
                    mm += 12
                    yy -= 1
                start = datetime(yy, mm, 1).date()
                if mm == 12:
                    end = datetime(yy + 1, 1, 1).date()
                else:
                    end = datetime(yy, mm + 1, 1).date()
                label = "本月" if i == 0 else f"{i}月前"
                buckets.append((label, start, end))
        return g, buckets

    def _trim_leading_empty(series: list, has_data) -> list:
        """Drop leading empty buckets; keep mid-gap zeros. No data → last bucket only."""
        if not series:
            return series
        for i, item in enumerate(series):
            if has_data(item):
                return series[i:]
        return series[-1:]

    @app.get("/api/stats/forgetting-curve")
    def stats_forgetting_curve():
        points = theory_curve_points(11)
        # Empirical retention by gap days between consecutive non-skip reviews of same sentence
        buckets = {d: {"success": 0, "total": 0} for d in range(12)}
        with get_db() as db:
            rows = db.execute(
                """SELECT sentence_id, reviewed_at, result FROM review_events
                   WHERE result != 'skipped' AND sentence_id IS NOT NULL
                   ORDER BY sentence_id, reviewed_at, id"""
            ).fetchall()
        by_sentence: dict[int, list] = {}
        for row in rows:
            by_sentence.setdefault(row["sentence_id"], []).append(row)
        for events in by_sentence.values():
            for i in range(1, len(events)):
                prev_dt = parse_iso(events[i - 1]["reviewed_at"])
                cur_dt = parse_iso(events[i]["reviewed_at"])
                if not prev_dt or not cur_dt:
                    continue
                gap = max(0, (cur_dt - prev_dt).total_seconds() / 86400.0)
                gap_day = int(gap)  # floor
                if gap_day > 11:
                    continue
                buckets[gap_day]["total"] += 1
                if events[i]["result"] in SUCCESS_RESULTS:
                    buckets[gap_day]["success"] += 1
        ready_count = 0
        for point in points:
            d = point["offsetDays"]
            total = buckets[d]["total"]
            point["userSampleSize"] = total
            empirical = None
            if total > 0:
                empirical = buckets[d]["success"] / total * 100
            if total >= MIN_CURVE_SAMPLES:
                ready_count += 1
            # Always emit a user value: prior = theory when samples are sparse.
            point["user"] = blend_user_rate(point["theory"], empirical, total)
        return jsonify(points=points, dataReady=ready_count >= 3, minSamples=MIN_CURVE_SAMPLES)

    @app.get("/api/stats/learning")
    def stats_learning():
        granularity = request.args.get("granularity", "day")
        g, buckets = _bucket_specs(granularity)
        series = []
        earliest_local_date = buckets[0][1] - timedelta(days=2)
        lower_bound = datetime.combine(
            earliest_local_date, datetime.min.time(), tzinfo=timezone.utc
        ).isoformat(timespec="seconds")
        with get_db() as db:
            events = [
                dict(row)
                for row in db.execute(
                    "SELECT reviewed_at, result, is_new, duration_ms FROM review_events WHERE reviewed_at >= ? ORDER BY reviewed_at",
                    (lower_bound,),
                ).fetchall()
            ]
            now = now_iso()
            due_total = db.execute(
                "SELECT COUNT(*) n FROM sentences WHERE next_review_at<=?", (now,)
            ).fetchone()["n"]

        # Pre-group events by local date
        by_date: dict = {}
        for ev in events:
            dt = parse_iso(ev["reviewed_at"])
            if not dt:
                continue
            d = local_date(dt)
            by_date.setdefault(d, []).append(ev)

        today = local_date()
        today_counts = {"known": 0, "fuzzy": 0, "forgotten": 0, "skipped": 0}
        today_duration_ms = 0
        for ev in by_date.get(today, []):
            r = ev["result"]
            if r in today_counts:
                today_counts[r] += 1
            today_duration_ms += int(ev.get("duration_ms") or 0)

        for label, start, end in buckets:
            counts = {
                "known": 0, "fuzzy": 0, "forgotten": 0,
                "new": 0, "review": 0,
            }
            d = start
            while d < end:
                for ev in by_date.get(d, []):
                    r = ev["result"]
                    if r in COGNITIVE_RESULTS:
                        counts[r] += 1
                    if r != "skipped":
                        if int(ev.get("is_new") or 0):
                            counts["new"] += 1
                        else:
                            counts["review"] += 1
                d += timedelta(days=1)
            series.append({"label": label, "start": start.isoformat(), "end": end.isoformat(), **counts})

        def _learning_bucket_has_data(item: dict) -> bool:
            return (
                item.get("known", 0)
                + item.get("fuzzy", 0)
                + item.get("forgotten", 0)
                + item.get("new", 0)
                + item.get("review", 0)
            ) > 0

        series = _trim_leading_empty(series, _learning_bucket_has_data)

        pressure = due_total >= DUE_PRESSURE_THRESHOLD
        return jsonify(
            granularity=g,
            series=series,
            today={
                "known": today_counts["known"],
                "fuzzy": today_counts["fuzzy"],
                "forgotten": today_counts["forgotten"],
                "dueTotal": due_total,
                "durationSec": round(today_duration_ms / 1000),
            },
            pressureHint=pressure,
            pressureMessage="待复习句子较多，可分散复习减轻压力" if pressure else "",
        )

    @app.get("/api/stats/retention")
    def stats_retention():
        granularity = request.args.get("granularity", "week")
        g, buckets = _bucket_specs(granularity)
        earliest_local_date = buckets[0][1] - timedelta(days=2)
        lower_bound = datetime.combine(
            earliest_local_date, datetime.min.time(), tzinfo=timezone.utc
        ).isoformat(timespec="seconds")
        with get_db() as db:
            sentences = [
                dict(row)
                for row in db.execute("SELECT id, created_at, stability FROM sentences").fetchall()
            ]
            events = [
                dict(row)
                for row in db.execute(
                    """SELECT sentence_id, reviewed_at, stability_after FROM review_events
                       WHERE sentence_id IS NOT NULL AND result != 'skipped' AND reviewed_at >= ?
                       ORDER BY sentence_id, reviewed_at, id""",
                    (lower_bound,),
                ).fetchall()
            ]

        # 每条事件的本地日期只解析一次；按 sentence_id 分组，组内已按 reviewed_at 有序。
        timelines: dict[int, list[tuple]] = {}
        for ev in events:
            dt = parse_iso(ev["reviewed_at"])
            if not dt:
                continue
            timelines.setdefault(ev["sentence_id"], []).append((local_date(dt), ev.get("stability_after")))

        sent_created = []
        for sent in sentences:
            created = parse_iso(sent["created_at"])
            if not created:
                continue
            sent_created.append((sent["id"], local_date(created)))

        # 每个句子一个游标 [事件下标, 最后一次看到的 stability_after]，
        # 桶按时间升序遍历，cutoff 单调递增，游标只需单调前进，不需要每个桶重扫。
        cursors: dict[int, list] = {sid: [0, None] for sid, _ in sent_created}

        series = []
        for label, start, end in buckets:
            cutoff = end
            total = 0
            counts = {n: 0 for n in HOLD_THRESHOLDS}
            for sid, created_date in sent_created:
                if created_date >= cutoff:
                    continue
                total += 1
                tl = timelines.get(sid, [])
                cur = cursors[sid]
                idx, s = cur
                while idx < len(tl) and tl[idx][0] < cutoff:
                    if tl[idx][1] is not None:
                        s = float(tl[idx][1])
                    idx += 1
                cur[0], cur[1] = idx, s
                hd = hold_days(s if s is not None else INITIAL_S)
                for n in HOLD_THRESHOLDS:
                    if hd >= n:
                        counts[n] += 1
            item = {
                "label": label,
                "start": start.isoformat(),
                "end": end.isoformat(),
                "all": total,
                "allPct": 100.0 if total else 0.0,
            }
            for n in HOLD_THRESHOLDS:
                item[f"d{n}"] = counts[n]
                item[f"d{n}Pct"] = round(counts[n] * 100 / total, 1) if total else 0.0
            series.append(item)

        series = _trim_leading_empty(series, lambda item: (item.get("all") or 0) > 0)
        return jsonify(granularity=g, series=series, thresholds=list(HOLD_THRESHOLDS))

    # Build content-subset fonts at startup (no-op if sources missing / already current).
    try:
        ensure_active_fonts()
    except Exception:
        app.logger.exception("Initial active font build failed")

    return app


app = create_app()
