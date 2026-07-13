from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import time
from datetime import datetime, timedelta, timezone

from flask import Flask, jsonify, request, send_from_directory, session
from werkzeug.middleware.proxy_fix import ProxyFix

from auth import authed, clear, configured as auth_configured, fail, keys, lock_remaining
from db import get_db, init_db, json_load, now_iso, set_setting, setting
from security import hash_password, verify_password
from tokenizer import local_tokenize, validate_chunks

INTERVALS = [1, 3, 7, 14, 30]


def truthy(name: str, default=False):
    return os.environ.get(name, str(default)).lower() in {"1", "true", "yes", "on"}


def sentence_dict(row):
    data = dict(row)
    data["chunks"] = json_load(data.pop("chunks_json"), [])
    data["correctOrder"] = json_load(data.pop("correct_order_json"), [])
    return data


def sentence_snapshot(row):
    item = sentence_dict(row)
    return {key: item[key] for key in ("id", "chinese", "japanese", "chunks", "correctOrder")}


def answers_match(answer, correct):
    return (
        isinstance(answer, list)
        and isinstance(correct, list)
        and len(answer) == len(correct)
        and all(isinstance(value, str) and value == correct[index] for index, value in enumerate(answer))
    )


def stats_snapshot(row):
    return {key: row[key] for key in ("study_count", "correct_count", "wrong_count", "skip_count", "correct_streak", "next_review_at", "last_practiced_at")}


def apply_attempt_stats(db, row, sentence_id, status, stamp, base=None):
    base = base or stats_snapshot(row)
    study = int(base["study_count"]) + (status != "skipped")
    correct = int(base["correct_count"]) + (status == "correct")
    wrong = int(base["wrong_count"]) + (status == "wrong")
    skipped = int(base["skip_count"]) + (status == "skipped")
    streak = int(base["correct_streak"])
    due = base["next_review_at"]
    if status == "correct":
        streak += 1
        days = INTERVALS[min(streak - 1, len(INTERVALS) - 1)]
        due = (datetime.now(timezone.utc) + timedelta(days=days)).isoformat(timespec="seconds")
    elif status == "wrong":
        streak, due = 0, stamp
    db.execute("""UPDATE sentences SET study_count=?,correct_count=?,wrong_count=?,skip_count=?,correct_streak=?,next_review_at=?,last_practiced_at=?,updated_at=? WHERE id=?""",
               (study, correct, wrong, skipped, streak, due, stamp, stamp, sentence_id))


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
        if not request.path.startswith("/api/") or request.path in {"/api/health", "/api/auth/status", "/api/auth/login", "/api/auth/logout"}:
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
        today = datetime.now(timezone.utc).date().isoformat()
        now = now_iso()
        with get_db() as db:
            collections = [dict(row) for row in db.execute("""
              SELECT c.id,c.name,COUNT(s.id) total,
                SUM(CASE WHEN s.study_count>0 THEN 1 ELSE 0 END) learned
              FROM collections c LEFT JOIN sentences s ON s.collection_id=c.id
              GROUP BY c.id ORDER BY c.created_at
            """)]
            for item in collections:
                item["total"], item["learned"] = int(item["total"] or 0), int(item["learned"] or 0)
                item["due"] = db.execute("SELECT COUNT(*) n FROM sentences WHERE collection_id=? AND next_review_at<=?", (item["id"], now)).fetchone()["n"]
                item["today"] = db.execute("SELECT COUNT(DISTINCT sentence_id) n FROM attempts WHERE sentence_id IN (SELECT id FROM sentences WHERE collection_id=?) AND substr(created_at,1,10)=?", (item["id"], today)).fetchone()["n"]
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
                return jsonify(id=cursor.lastrowid, name=name), 201
        except Exception as exc:
            if "UNIQUE" in str(exc):
                return jsonify(error="句集名称已存在"), 409
            raise

    @app.patch("/api/collections/<int:collection_id>")
    def rename_collection(collection_id):
        name = str((request.get_json(silent=True) or {}).get("name", "")).strip()
        if not name:
            return jsonify(error="句集名称不能为空"), 400
        with get_db() as db:
            changed = db.execute("UPDATE collections SET name=?,updated_at=? WHERE id=?", (name, now_iso(), collection_id)).rowcount
        return (jsonify(ok=True) if changed else (jsonify(error="句集不存在"), 404))

    @app.delete("/api/collections/<int:collection_id>")
    def delete_collection(collection_id):
        with get_db() as db:
            count = db.execute("SELECT COUNT(*) n FROM sentences WHERE collection_id=?", (collection_id,)).fetchone()["n"]
            if count:
                return jsonify(error="请先移动或删除句集中的句子"), 409
            if db.execute("SELECT COUNT(*) n FROM collections").fetchone()["n"] <= 1:
                return jsonify(error="至少保留一个句集"), 409
            changed = db.execute("DELETE FROM collections WHERE id=?", (collection_id,)).rowcount
        return (jsonify(ok=True) if changed else (jsonify(error="句集不存在"), 404))

    @app.post("/api/sentences/organize")
    def organize():
        body = request.get_json(silent=True) or {}
        if not isinstance(body.get("japanese"), str) or not isinstance(body.get("chinese"), str):
            return jsonify(error="中文翻译和日语原句必须是字符串"), 400
        japanese, chinese = body["japanese"], body["chinese"].strip()
        if not japanese.strip() or not chinese:
            return jsonify(error="中文翻译和日语原句都不能为空"), 400
        return jsonify(chunks=local_tokenize(japanese), source="sudachi")

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
            with get_db() as db:
                cursor = db.execute("""INSERT INTO sentences(collection_id,chinese,japanese,chunks_json,correct_order_json,kana,romaji,explanation,next_review_at,created_at,updated_at)
                  VALUES(?,?,?,?,?,?,?,?,?,?,?)""", (item["collection_id"], item["chinese"], item["japanese"], json.dumps(item["chunks"], ensure_ascii=False), json.dumps(item["order"]), "", "", "", stamp, stamp, stamp))
                row = db.execute("SELECT * FROM sentences WHERE id=?", (cursor.lastrowid,)).fetchone()
            return jsonify(sentence=sentence_dict(row)), 201
        except Exception as exc:
            if "FOREIGN KEY" in str(exc):
                return jsonify(error="所属句集不存在"), 400
            raise

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
        with get_db() as db:
            changed = db.execute("""UPDATE sentences SET collection_id=?,chinese=?,japanese=?,chunks_json=?,correct_order_json=?,kana='',romaji='',explanation='',updated_at=? WHERE id=?""", (item["collection_id"], item["chinese"], item["japanese"], json.dumps(item["chunks"], ensure_ascii=False), json.dumps(item["order"]), now_iso(), sentence_id)).rowcount
        return jsonify(ok=True) if changed else (jsonify(error="句子不存在"), 404)

    @app.delete("/api/sentences/<int:sentence_id>")
    def delete_sentence(sentence_id):
        with get_db() as db:
            changed = db.execute("DELETE FROM sentences WHERE id=?", (sentence_id,)).rowcount
        return jsonify(ok=True) if changed else (jsonify(error="句子不存在"), 404)

    @app.post("/api/practice/sessions")
    def start_session():
        body = request.get_json(silent=True) or {}
        ids = body.get("sentenceIds")
        notice = ""
        with get_db() as db:
            if isinstance(ids, list) and ids:
                clean = [int(value) for value in ids]
                placeholders = ",".join("?" for _ in clean)
                rows = db.execute(f"SELECT id FROM sentences WHERE id IN ({placeholders})", clean).fetchall()
                selected = [row["id"] for row in rows]
                source = "selected"
            elif body.get("scope") == "collection" and body.get("collectionId"):
                collection_id = int(body["collectionId"])
                available = db.execute("SELECT COUNT(*) n FROM sentences WHERE collection_id=?", (collection_id,)).fetchone()["n"]
                if not available:
                    return jsonify(error="当前句集还没有句子"), 400
                requested = body.get("count")
                if requested in (None, "all"):
                    limit = available
                else:
                    try:
                        limit = max(1, int(requested))
                    except (TypeError, ValueError):
                        return jsonify(error="题目数量必须是正整数"), 400
                    if limit >= available:
                        if limit > available:
                            notice = f"当前句集只有 {available} 句，已调整为全部"
                        limit = available
                selected = [row["id"] for row in db.execute("SELECT id FROM sentences WHERE collection_id=? ORDER BY RANDOM() LIMIT ?", (collection_id, limit))]
                source = "collection"
            else:
                params, where = [now_iso()], "next_review_at<=?"
                if body.get("collectionId"):
                    where += " AND collection_id=?"; params.append(int(body["collectionId"]))
                selected = [row["id"] for row in db.execute(f"SELECT id FROM sentences WHERE {where} ORDER BY next_review_at,created_at", params)]
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
        sentence_id, action = int(body.get("sentenceId", 0)), str(body.get("action", "check"))
        answer = body.get("answerOrder")
        if not isinstance(answer, list):
            answer = []
        stamp = now_iso()
        with get_db() as db:
            practice = db.execute("SELECT * FROM practice_sessions WHERE id=?", (session_id,)).fetchone()
            row = db.execute("SELECT * FROM sentences WHERE id=?", (sentence_id,)).fetchone()
            if not practice or not row or sentence_id not in json_load(practice["sentence_ids_json"], []):
                return jsonify(error="练习或句子不存在"), 404
            item = sentence_dict(row)
            status = "skipped" if action == "skip" else ("correct" if answers_match(answer, item["correctOrder"]) else "wrong")
            previous = db.execute("SELECT * FROM attempts WHERE session_id=? AND sentence_id=? ORDER BY id LIMIT 1", (session_id, sentence_id)).fetchone()
            if previous:
                base = json_load(previous["stats_before_json"], None) or stats_snapshot(row)
                db.execute("UPDATE attempts SET status=?,answer_order_json=?,sentence_snapshot_json=?,created_at=? WHERE id=?", (status, json.dumps(answer), json.dumps(sentence_snapshot(row), ensure_ascii=False), stamp, previous["id"]))
            else:
                base = stats_snapshot(row)
                db.execute("INSERT INTO attempts(session_id,sentence_id,status,answer_order_json,sentence_snapshot_json,stats_before_json,created_at) VALUES(?,?,?,?,?,?,?)", (session_id, sentence_id, status, json.dumps(answer), json.dumps(sentence_snapshot(row), ensure_ascii=False), json.dumps(base), stamp))
            apply_attempt_stats(db, row, sentence_id, status, stamp, base)
        return jsonify(status=status, correctOrder=item["correctOrder"], correct=status == "correct")

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

    return app


app = create_app()
