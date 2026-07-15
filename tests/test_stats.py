"""API tests for stats endpoints, scheduler settings, and dynamic SRS."""
import importlib
import json
from datetime import datetime, timedelta, timezone


def load_app(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("APP_SECRET", "test-secret")
    monkeypatch.setenv("SESSION_COOKIE_SECURE", "false")
    monkeypatch.setenv("TRUST_PROXY_COUNT", "0")
    import db, app
    importlib.reload(db)
    importlib.reload(app)
    return app.create_app({"TESTING": True}).test_client(), db


def make_sentence(client, collection, text="文"):
    chunks = [{"id": f"c-{text}", "text": text}]
    created = client.post("/api/sentences", json={
        "collectionId": collection,
        "chinese": f"中{text}",
        "japanese": text,
        "chunks": chunks,
        "correctOrder": [chunks[0]["id"]],
    })
    assert created.status_code == 201
    return created.get_json()["sentence"]


def test_scheduler_settings_roundtrip(tmp_path, monkeypatch):
    client, _ = load_app(tmp_path, monkeypatch)
    got = client.get("/api/settings/scheduler").get_json()
    assert got["mode"] == "dynamic"
    assert client.put("/api/settings/scheduler", json={"mode": "fixed"}).status_code == 200
    assert client.get("/api/settings/scheduler").get_json()["mode"] == "fixed"
    assert client.put("/api/settings/scheduler", json={"mode": "nope"}).status_code == 400


def test_dynamic_srs_updates_stability_and_due(tmp_path, monkeypatch):
    client, db = load_app(tmp_path, monkeypatch)
    collection = client.get("/api/dashboard").get_json()["collections"][0]["id"]
    sentence = make_sentence(client, collection, "あ")
    practice = client.post("/api/practice/sessions", json={"sentenceIds": [sentence["id"]]}).get_json()
    endpoint = f'/api/practice/sessions/{practice["sessionId"]}/attempts'
    # First correct → known (duration no longer affects grade)
    res = client.post(endpoint, json={
        "sentenceId": sentence["id"],
        "action": "check",
        "answerOrder": sentence["correctOrder"],
        "durationMs": 3000,
    })
    assert res.status_code == 200
    assert res.get_json()["grade"] == "known"
    refreshed = client.get(f'/api/sentences/{sentence["id"]}').get_json()["sentence"]
    assert refreshed["stability"] > 1.0
    assert refreshed["review_count"] == 1
    assert refreshed["next_review_at"] > refreshed["last_practiced_at"]

    with db.get_db() as connection:
        events = connection.execute(
            "SELECT result, attempt_n FROM review_events WHERE sentence_id=?",
            (sentence["id"],),
        ).fetchall()
    assert len(events) == 1
    assert events[0]["result"] == "known"


def test_retry_grades_as_fuzzy_and_upserts_event(tmp_path, monkeypatch):
    client, db = load_app(tmp_path, monkeypatch)
    collection = client.get("/api/dashboard").get_json()["collections"][0]["id"]
    sentence = make_sentence(client, collection, "い")
    practice = client.post("/api/practice/sessions", json={"sentenceIds": [sentence["id"]]}).get_json()
    endpoint = f'/api/practice/sessions/{practice["sessionId"]}/attempts'
    wrong = client.post(endpoint, json={
        "sentenceId": sentence["id"], "action": "check",
        "answerOrder": [], "durationMs": 1000,
    })
    assert wrong.get_json()["status"] == "wrong"
    final = client.post(endpoint, json={
        "sentenceId": sentence["id"], "action": "check",
        "answerOrder": sentence["correctOrder"], "durationMs": 2000,
    })
    assert final.get_json()["grade"] == "fuzzy"
    with db.get_db() as connection:
        n = connection.execute(
            "SELECT COUNT(*) n FROM review_events WHERE sentence_id=?",
            (sentence["id"],),
        ).fetchone()["n"]
        row = connection.execute(
            "SELECT result, attempt_n FROM review_events WHERE sentence_id=?",
            (sentence["id"],),
        ).fetchone()
    assert n == 1
    assert row["result"] == "fuzzy"
    assert row["attempt_n"] == 2


def test_fixed_mode_uses_interval_ladder(tmp_path, monkeypatch):
    client, _ = load_app(tmp_path, monkeypatch)
    client.put("/api/settings/scheduler", json={"mode": "fixed"})
    collection = client.get("/api/dashboard").get_json()["collections"][0]["id"]
    sentence = make_sentence(client, collection, "う")
    practice = client.post("/api/practice/sessions", json={"sentenceIds": [sentence["id"]]}).get_json()
    client.post(
        f'/api/practice/sessions/{practice["sessionId"]}/attempts',
        json={"sentenceId": sentence["id"], "action": "check", "answerOrder": sentence["correctOrder"], "durationMs": 20000},
    )
    refreshed = client.get(f'/api/sentences/{sentence["id"]}').get_json()["sentence"]
    # streak 1 → 1 day fixed
    from datetime import datetime
    due = datetime.fromisoformat(refreshed["next_review_at"])
    practiced = datetime.fromisoformat(refreshed["last_practiced_at"])
    delta = (due - practiced).total_seconds()
    assert 23 * 3600 < delta < 25 * 3600


def test_learning_stats_buckets_and_today(tmp_path, monkeypatch):
    client, db = load_app(tmp_path, monkeypatch)
    collection = client.get("/api/dashboard").get_json()["collections"][0]["id"]
    s1 = make_sentence(client, collection, "え")
    s2 = make_sentence(client, collection, "お")
    stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with db.get_db() as connection:
        for sid, result, is_new, ms in [
            (s1["id"], "known", 1, 5000),
            (s1["id"], "fuzzy", 0, 8000),  # will be separate rows for historical
            (s2["id"], "forgotten", 1, 12000),
        ]:
            connection.execute(
                """INSERT INTO review_events(
                     sentence_id, session_id, reviewed_at, result, duration_ms, attempt_n,
                     is_new, stability_before, stability_after, interval_days, created_at
                   ) VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                (sid, None, stamp, result, ms, 1, is_new, 1.0, 2.0 if result != "forgotten" else 1.0, 1.0, stamp),
            )
    data = client.get("/api/stats/learning?granularity=day").get_json()
    assert data["granularity"] == "day"
    # Only today has activity → leading empty buckets trimmed; grow from first data day
    assert len(data["series"]) == 1
    assert data["series"][0]["label"] == "今天"
    today = data["today"]
    assert "mastered" not in today
    assert today["known"] >= 1
    assert today["fuzzy"] >= 1
    assert today["forgotten"] >= 1
    assert today["durationSec"] >= 5
    assert "dueTotal" in today
    assert data["series"][-1]["known"] >= 1


def test_dashboard_today_aligns_with_stats_across_utc_boundary(tmp_path, monkeypatch):
    """Dashboard and stats_learning must share local-day + review_events for 'today'.

    Construct events where UTC calendar date may differ from the server local date
    (common for Asia/Shanghai near the UTC day boundary). Old dashboard used
    substr(attempts.created_at,1,10) against UTC date and would miscount.
    """
    from memory import local_date, parse_iso

    client, db = load_app(tmp_path, monkeypatch)
    collection = client.get("/api/dashboard").get_json()["collections"][0]["id"]
    s_today = make_sentence(client, collection, "跨日A")
    s_skipped = make_sentence(client, collection, "跨日B")
    s_tomorrow = make_sentence(client, collection, "跨日C")

    today = local_date()
    local_tz = datetime.now().astimezone().tzinfo
    # Local today 01:00 — often still previous UTC date for +HH:MM zones.
    local_morning = (
        datetime.combine(today, datetime.min.time()).replace(tzinfo=local_tz)
        + timedelta(hours=1)
    )
    stamp_today = local_morning.astimezone(timezone.utc).isoformat(timespec="seconds")
    # Local tomorrow 01:00 — must not count toward "today".
    stamp_tomorrow = (
        local_morning + timedelta(days=1)
    ).astimezone(timezone.utc).isoformat(timespec="seconds")

    assert local_date(parse_iso(stamp_today)) == today
    assert local_date(parse_iso(stamp_tomorrow)) == today + timedelta(days=1)

    with db.get_db() as connection:
        for sid, result, stamp in [
            (s_today["id"], "known", stamp_today),
            (s_skipped["id"], "skipped", stamp_today),
            (s_tomorrow["id"], "known", stamp_tomorrow),
        ]:
            connection.execute(
                """INSERT INTO review_events(
                     sentence_id, session_id, reviewed_at, result, duration_ms, attempt_n,
                     is_new, stability_before, stability_after, interval_days, created_at
                   ) VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                (sid, None, stamp, result, 5000, 1, 1, 1.0, 2.0, 1.0, stamp),
            )

    dash = client.get("/api/dashboard").get_json()
    col = next(c for c in dash["collections"] if c["id"] == collection)
    # Only the non-skipped local-today sentence (distinct) counts.
    assert col["today"] == 1

    stats = client.get("/api/stats/learning?granularity=day").get_json()
    # Same local day: the known event is included; tomorrow's is not.
    assert stats["today"]["known"] == 1
    assert stats["today"]["fuzzy"] == 0
    assert stats["today"]["forgotten"] == 0


def test_learning_stats_trims_leading_keeps_mid_gap(tmp_path, monkeypatch):
    client, db = load_app(tmp_path, monkeypatch)
    collection = client.get("/api/dashboard").get_json()["collections"][0]["id"]
    s = make_sentence(client, collection, "え2")
    now = datetime.now(timezone.utc)
    # Activity 5 days ago and today; days in between empty → continuous axis with zeros
    stamps = [
        (now - timedelta(days=5)).isoformat(timespec="seconds"),
        now.isoformat(timespec="seconds"),
    ]
    with db.get_db() as connection:
        for t in stamps:
            connection.execute(
                """INSERT INTO review_events(
                     sentence_id, session_id, reviewed_at, result, duration_ms, attempt_n,
                     is_new, stability_before, stability_after, interval_days, created_at
                   ) VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                (s["id"], None, t, "known", 1000, 1, 0, 1.0, 2.0, 1.0, t),
            )
    data = client.get("/api/stats/learning?granularity=day").get_json()
    series = data["series"]
    assert len(series) == 6  # 5 days ago … today inclusive
    assert series[0]["label"] == "5天前"
    assert series[-1]["label"] == "今天"
    # Mid gap: day 4..1 ago should be zero-activity placeholders
    mid = series[1:-1]
    assert len(mid) == 4
    for bucket in mid:
        activity = (
            bucket["known"] + bucket["fuzzy"]
            + bucket["forgotten"] + bucket["new"] + bucket["review"]
        )
        assert activity == 0


def test_forgetting_curve_empirical_buckets(tmp_path, monkeypatch):
    client, db = load_app(tmp_path, monkeypatch)
    collection = client.get("/api/dashboard").get_json()["collections"][0]["id"]
    sentence = make_sentence(client, collection, "か")
    base = datetime.now(timezone.utc) - timedelta(days=20)
    with db.get_db() as connection:
        # Create enough pairs with gap=1 day, all successful, to pass min samples
        for i in range(6):
            t1 = (base + timedelta(days=i * 3)).isoformat(timespec="seconds")
            t2 = (base + timedelta(days=i * 3 + 1)).isoformat(timespec="seconds")
            for t, result in [(t1, "known"), (t2, "known")]:
                connection.execute(
                    """INSERT INTO review_events(
                         sentence_id, session_id, reviewed_at, result, duration_ms, attempt_n,
                         is_new, stability_before, stability_after, interval_days, created_at
                       ) VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                    (sentence["id"], None, t, result, 1000, 1, 0, 1.0, 2.0, 1.0, t),
                )
    data = client.get("/api/stats/forgetting-curve").get_json()
    assert len(data["points"]) == 12
    assert data["points"][0]["theory"] == 100.0
    # Theory strictly decreasing
    theories = [p["theory"] for p in data["points"]]
    assert all(theories[i] > theories[i + 1] for i in range(len(theories) - 1))
    assert 33.0 <= theories[1] <= 44.0
    # User curve always present (never null); sparse buckets equal theory
    for p in data["points"]:
        assert p["user"] is not None
        if p["userSampleSize"] == 0:
            assert p["user"] == p["theory"]
    gap1 = data["points"][1]
    assert gap1["userSampleSize"] >= 3
    # 100% empirical blended with theory → between theory and 100
    assert gap1["theory"] < gap1["user"] <= 100.0


def test_forgetting_curve_empty_user_matches_theory(tmp_path, monkeypatch):
    client, _ = load_app(tmp_path, monkeypatch)
    data = client.get("/api/stats/forgetting-curve").get_json()
    assert len(data["points"]) == 12
    assert data["dataReady"] is False
    for p in data["points"]:
        assert p["userSampleSize"] == 0
        assert p["user"] == p["theory"]


def test_retention_stats_thresholds(tmp_path, monkeypatch):
    client, db = load_app(tmp_path, monkeypatch)
    collection = client.get("/api/dashboard").get_json()["collections"][0]["id"]
    s = make_sentence(client, collection, "き")
    # S such that hold_days >= 10: need -S*ln(0.9) >= 10 → S >= 10/0.10536 ≈ 95
    stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with db.get_db() as connection:
        connection.execute(
            "UPDATE sentences SET stability=?, created_at=? WHERE id=?",
            (100.0, stamp, s["id"]),
        )
        connection.execute(
            """INSERT INTO review_events(
                 sentence_id, session_id, reviewed_at, result, duration_ms, attempt_n,
                 is_new, stability_before, stability_after, interval_days, created_at
               ) VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
            (s["id"], None, stamp, "known", 1000, 1, 1, 1.0, 100.0, 10.0, stamp),
        )
    data = client.get("/api/stats/retention?granularity=week").get_json()
    assert data["granularity"] == "week"
    # Sentence created today → series starts this week (leading empty weeks trimmed)
    assert len(data["series"]) >= 1
    assert data["series"][-1]["label"] == "本周"
    last = data["series"][-1]
    assert last["all"] >= 1
    assert last["d10"] >= 1
    assert last["d90"] == 0  # 100 * ln isn't enough for 90 days at 90%


def test_retention_mid_gap_still_has_points(tmp_path, monkeypatch):
    """Memory snapshot exists every day after first sentence; mid days are not dropped."""
    client, db = load_app(tmp_path, monkeypatch)
    collection = client.get("/api/dashboard").get_json()["collections"][0]["id"]
    s = make_sentence(client, collection, "き2")
    created = (datetime.now(timezone.utc) - timedelta(days=4)).isoformat(timespec="seconds")
    with db.get_db() as connection:
        connection.execute(
            "UPDATE sentences SET stability=?, created_at=? WHERE id=?",
            (100.0, created, s["id"]),
        )
    data = client.get("/api/stats/retention?granularity=day").get_json()
    series = data["series"]
    # From created day (4 days ago) through today → 5 continuous buckets
    assert len(series) == 5
    for bucket in series:
        assert bucket["all"] >= 1


def test_learning_series_can_exceed_visible_bucket_threshold(tmp_path, monkeypatch):
    """Long continuous history returns enough buckets for frontend horizontal scroll."""
    client, db = load_app(tmp_path, monkeypatch)
    collection = client.get("/api/dashboard").get_json()["collections"][0]["id"]
    s = make_sentence(client, collection, "く2")
    now = datetime.now(timezone.utc)
    # 20 days of activity (day threshold is 14)
    with db.get_db() as connection:
        for i in range(20, -1, -1):
            t = (now - timedelta(days=i)).isoformat(timespec="seconds")
            connection.execute(
                """INSERT INTO review_events(
                     sentence_id, session_id, reviewed_at, result, duration_ms, attempt_n,
                     is_new, stability_before, stability_after, interval_days, created_at
                   ) VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                (s["id"], None, t, "known", 500, 1, 0, 1.0, 2.0, 1.0, t),
            )
    data = client.get("/api/stats/learning?granularity=day").get_json()
    assert len(data["series"]) == 21
    assert len(data["series"]) > 14  # exceeds VISIBLE_BUCKETS.day


def test_delete_sentence_hard_deletes_review_history(tmp_path, monkeypatch):
    client, db = load_app(tmp_path, monkeypatch)
    collection = client.get("/api/dashboard").get_json()["collections"][0]["id"]
    sentence = make_sentence(client, collection, "け")
    practice = client.post("/api/practice/sessions", json={"sentenceIds": [sentence["id"]]}).get_json()
    client.post(
        f'/api/practice/sessions/{practice["sessionId"]}/attempts',
        json={
            "sentenceId": sentence["id"],
            "action": "check",
            "answerOrder": sentence["correctOrder"],
            "durationMs": 5000,
        },
    )
    with db.get_db() as connection:
        assert connection.execute(
            "SELECT COUNT(*) n FROM review_events WHERE sentence_id=?", (sentence["id"],)
        ).fetchone()["n"] == 1
        assert connection.execute(
            "SELECT COUNT(*) n FROM attempts WHERE sentence_id=?", (sentence["id"],)
        ).fetchone()["n"] >= 1

    assert client.delete(f'/api/sentences/{sentence["id"]}').status_code == 200

    with db.get_db() as connection:
        assert connection.execute(
            "SELECT COUNT(*) n FROM sentences WHERE id=?", (sentence["id"],)
        ).fetchone()["n"] == 0
        assert connection.execute(
            "SELECT COUNT(*) n FROM review_events WHERE sentence_id=?", (sentence["id"],)
        ).fetchone()["n"] == 0
        assert connection.execute(
            "SELECT COUNT(*) n FROM attempts WHERE sentence_id=?", (sentence["id"],)
        ).fetchone()["n"] == 0
        # No orphaned NULL sentence_id rows from this delete path
        assert connection.execute(
            "SELECT COUNT(*) n FROM review_events WHERE sentence_id IS NULL"
        ).fetchone()["n"] == 0


def test_migration_adds_columns_and_backfills(tmp_path, monkeypatch):
    client, db = load_app(tmp_path, monkeypatch)
    collection = client.get("/api/dashboard").get_json()["collections"][0]["id"]
    sentence = make_sentence(client, collection, "く")
    practice = client.post("/api/practice/sessions", json={"sentenceIds": [sentence["id"]]}).get_json()
    client.post(
        f'/api/practice/sessions/{practice["sessionId"]}/attempts',
        json={"sentenceId": sentence["id"], "action": "check", "answerOrder": sentence["correctOrder"]},
    )
    with db.get_db() as connection:
        cols = {row["name"] for row in connection.execute("PRAGMA table_info(sentences)")}
        assert {"stability", "review_count", "lapse_count"} <= cols
        a_cols = {row["name"] for row in connection.execute("PRAGMA table_info(attempts)")}
        assert {"duration_ms", "attempt_n", "grade"} <= a_cols
        assert connection.execute("SELECT COUNT(*) n FROM review_events").fetchone()["n"] >= 1
    # Re-init is idempotent
    db.init_db()
    with db.get_db() as connection:
        n1 = connection.execute("SELECT COUNT(*) n FROM review_events").fetchone()["n"]
    db.init_db()
    with db.get_db() as connection:
        n2 = connection.execute("SELECT COUNT(*) n FROM review_events").fetchone()["n"]
    assert n1 == n2


def test_retry_wrong_session_first_correct_is_fuzzy(tmp_path, monkeypatch):
    """retryWrong sessions grade first correct as fuzzy (not known)."""
    client, db = load_app(tmp_path, monkeypatch)
    collection = client.get("/api/dashboard").get_json()["collections"][0]["id"]
    sentence = make_sentence(client, collection, "さ")
    practice = client.post(
        "/api/practice/sessions",
        json={"sentenceIds": [sentence["id"]], "retryWrong": True},
    ).get_json()
    with db.get_db() as connection:
        source = connection.execute(
            "SELECT source FROM practice_sessions WHERE id=?",
            (practice["sessionId"],),
        ).fetchone()["source"]
    assert source == "retry_wrong"
    res = client.post(
        f'/api/practice/sessions/{practice["sessionId"]}/attempts',
        json={
            "sentenceId": sentence["id"],
            "action": "check",
            "answerOrder": sentence["correctOrder"],
            "durationMs": 4000,
        },
    )
    assert res.status_code == 200
    assert res.get_json()["grade"] == "fuzzy"
    with db.get_db() as connection:
        row = connection.execute(
            "SELECT result, attempt_n FROM review_events WHERE sentence_id=?",
            (sentence["id"],),
        ).fetchone()
    assert row["result"] == "fuzzy"
    assert row["attempt_n"] == 1


def test_retry_wrong_e2e_after_failed_round(tmp_path, monkeypatch):
    """Complete a round with wrongs → open retry_wrong session → correct → fuzzy."""
    client, db = load_app(tmp_path, monkeypatch)
    collection = client.get("/api/dashboard").get_json()["collections"][0]["id"]
    sentence = make_sentence(client, collection, "し")
    practice1 = client.post(
        "/api/practice/sessions", json={"sentenceIds": [sentence["id"]]},
    ).get_json()
    sid1 = practice1["sessionId"]
    wrong = client.post(
        f"/api/practice/sessions/{sid1}/attempts",
        json={
            "sentenceId": sentence["id"],
            "action": "check",
            "answerOrder": [],
            "durationMs": 1000,
        },
    )
    assert wrong.get_json()["status"] == "wrong"
    assert wrong.get_json()["grade"] == "forgotten"
    assert client.post(f"/api/practice/sessions/{sid1}/complete").status_code == 200

    practice2 = client.post(
        "/api/practice/sessions",
        json={"sentenceIds": [sentence["id"]], "retryWrong": True},
    ).get_json()
    sid2 = practice2["sessionId"]
    assert sid2 != sid1
    final = client.post(
        f"/api/practice/sessions/{sid2}/attempts",
        json={
            "sentenceId": sentence["id"],
            "action": "check",
            "answerOrder": sentence["correctOrder"],
            "durationMs": 2000,
        },
    )
    assert final.get_json()["grade"] == "fuzzy"
    with db.get_db() as connection:
        # Latest event for this sentence in the retry session is fuzzy
        row = connection.execute(
            """SELECT result FROM review_events
               WHERE sentence_id=? AND session_id=? ORDER BY id DESC LIMIT 1""",
            (sentence["id"], sid2),
        ).fetchone()
    assert row["result"] == "fuzzy"


def test_migrate_mastered_to_known(tmp_path, monkeypatch):
    client, db = load_app(tmp_path, monkeypatch)
    collection = client.get("/api/dashboard").get_json()["collections"][0]["id"]
    sentence = make_sentence(client, collection, "す")
    stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with db.get_db() as connection:
        connection.execute(
            """INSERT INTO review_events(
                 sentence_id, session_id, reviewed_at, result, duration_ms, attempt_n,
                 is_new, stability_before, stability_after, interval_days, created_at
               ) VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
            (sentence["id"], None, stamp, "mastered", 1000, 1, 1, 1.0, 2.0, 1.0, stamp),
        )
        practice = connection.execute(
            "INSERT INTO practice_sessions(source,sentence_ids_json,total,created_at) VALUES(?,?,?,?)",
            ("selected", json.dumps([sentence["id"]]), 1, stamp),
        )
        session_id = practice.lastrowid
        connection.execute(
            """INSERT INTO attempts(
                 session_id,sentence_id,status,answer_order_json,sentence_snapshot_json,
                 stats_before_json,duration_ms,attempt_n,grade,created_at
               ) VALUES(?,?,?,?,?,?,?,?,?,?)""",
            (
                session_id, sentence["id"], "correct", "[]", "{}",
                "{}", 1000, 1, "mastered", stamp,
            ),
        )
    db.init_db()
    with db.get_db() as connection:
        ev = connection.execute(
            "SELECT result FROM review_events WHERE sentence_id=?",
            (sentence["id"],),
        ).fetchone()
        att = connection.execute(
            "SELECT grade FROM attempts WHERE sentence_id=?",
            (sentence["id"],),
        ).fetchone()
        leftover = connection.execute(
            "SELECT COUNT(*) n FROM review_events WHERE result='mastered'"
        ).fetchone()["n"]
    assert ev["result"] == "known"
    assert att["grade"] == "known"
    assert leftover == 0
