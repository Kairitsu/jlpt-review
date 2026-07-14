"""Unit tests for the exponential forgetting model and grade mapping."""
import math

from memory import (
    FAST_MS,
    FIXED_INTERVALS,
    INITIAL_S,
    S_REF,
    TARGET_R,
    blend_user_rate,
    ebbinghaus_theory_rate,
    fixed_interval_days,
    grade_attempt,
    hold_days,
    interval_for_stability,
    retention,
    schedule_next,
    theory_curve_points,
    update_stability,
)


def test_grade_mapping_rules():
    assert grade_attempt("skipped") == "skipped"
    assert grade_attempt("wrong") == "forgotten"
    assert grade_attempt("correct", attempt_n=2) == "fuzzy"
    assert grade_attempt("correct", attempt_n=1, duration_ms=5_000) == "mastered"
    assert grade_attempt("correct", attempt_n=1, duration_ms=FAST_MS) == "mastered"
    assert grade_attempt("correct", attempt_n=1, duration_ms=FAST_MS + 1) == "known"
    assert grade_attempt("correct", attempt_n=1, duration_ms=0) == "known"


def test_stability_updates_and_clamp():
    s0 = INITIAL_S
    s_mastered = update_stability(s0, "mastered")
    s_known = update_stability(s0, "known")
    s_fuzzy = update_stability(s0, "fuzzy")
    s_forgot = update_stability(10.0, "forgotten")
    assert s_mastered > s_known > s_fuzzy > s0
    assert s_forgot == INITIAL_S
    assert update_stability(s0, "skipped") == s0
    # clamp upper
    assert update_stability(300.0, "mastered") <= 365.0


def test_interval_and_retention_math():
    s = 10.0
    t = interval_for_stability(s, TARGET_R)
    assert abs(t - (-s * math.log(TARGET_R))) < 1e-9
    assert abs(retention(t, s) - TARGET_R) < 1e-9
    assert hold_days(s) == t


def test_schedule_dynamic_and_fixed():
    from datetime import datetime, timezone

    now = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
    due, days = schedule_next(
        mode="dynamic", result="known", stability_after=10.0, streak_after=1, now=now,
    )
    assert days == interval_for_stability(10.0)
    assert due.startswith("2026-")

    due_f, days_f = schedule_next(
        mode="fixed", result="known", stability_after=10.0, streak_after=1, now=now,
    )
    assert days_f == float(FIXED_INTERVALS[0])
    assert "2026-01-02" in due_f

    due_w, days_w = schedule_next(
        mode="dynamic", result="forgotten", stability_after=1.0, streak_after=0, now=now,
    )
    assert days_w == 0.0
    assert due_w == now.isoformat(timespec="seconds")


def test_fixed_interval_ladder():
    assert fixed_interval_days(1) == 1
    assert fixed_interval_days(2) == 3
    assert fixed_interval_days(5) == 30
    assert fixed_interval_days(99) == 30


def test_theory_curve_shape():
    points = theory_curve_points(11)
    assert len(points) == 12
    assert points[0]["label"] == "今天"
    assert points[0]["theory"] == 100.0
    # Monotone decreasing exponential (day units); allow tiny late-window floor
    for i in range(len(points) - 1):
        assert points[i]["theory"] >= points[i + 1]["theory"]
    # Early window must drop visibly (not flatten after day 1)
    assert points[0]["theory"] > points[1]["theory"] > points[2]["theory"]
    assert points[2]["theory"] > points[6]["theory"] > points[11]["theory"]
    d1 = points[1]["theory"]
    assert 33.0 <= d1 <= 44.0
    # Day-unit model: R(t)=exp(-t/S_REF), not minute-log flattening
    assert abs(ebbinghaus_theory_rate(1) - math.exp(-1 / S_REF)) < 1e-9
    assert abs(ebbinghaus_theory_rate(0) - 1.0) < 1e-12
    # Unrounded rates are strictly decreasing across the whole window
    raw = [ebbinghaus_theory_rate(d) for d in range(12)]
    assert all(raw[i] > raw[i + 1] for i in range(11))


def test_blend_user_rate_prior_and_weight():
    theory = 40.0
    # No samples → equals theory prior
    assert blend_user_rate(theory, None, 0) == theory
    assert blend_user_rate(theory, 100.0, 0) == theory
    # With samples, pull toward empirical
    blended = blend_user_rate(theory, 100.0, 3)
    assert theory < blended < 100.0
    # More samples → closer to empirical
    more = blend_user_rate(theory, 100.0, 30)
    assert more > blended
