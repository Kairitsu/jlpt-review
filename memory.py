"""Exponential forgetting model and grade mapping for sentence review.

Grade mapping from practice outcomes (documented for stats UI 熟知/认识/模糊/忘记):

  - skipped  → skipped     (不计入认知堆叠 / 遗忘拟合)
  - wrong    → forgotten   (忘记)
  - correct & attempt_n > 1 → fuzzy     (模糊：多次尝试后对)
  - correct & attempt_n == 1 & duration_ms ≤ FAST_MS → mastered  (熟知)
  - correct & attempt_n == 1 & slower / no duration → known     (认识)

Memory model: R(t) = exp(-t / S)
Next interval: t = -S * ln(TARGET_R)  so review lands near the target retention.
"""
from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone

# --- Model constants ---
TARGET_R = 0.90
INITIAL_S = 1.0
MIN_S = 0.3
MAX_S = 365.0
FAST_MS = 15_000
FIXED_INTERVALS = [1, 3, 7, 14, 30]
DEFAULT_SCHEDULER_MODE = "dynamic"
MIN_CURVE_SAMPLES = 3
# Reference Ebbinghaus curve: R(t)=exp(-t/S_REF) in days.
# S_REF≈1.09 → day-1 retention ≈40% (classic 33–44% band); strictly decreasing after.
S_REF = 1.09
# Bayesian-style blend: w = n/(n+K); small n stays close to theory.
CURVE_BLEND_K = MIN_CURVE_SAMPLES
HOLD_THRESHOLDS = (10, 30, 60, 90)
DUE_PRESSURE_THRESHOLD = 30

RESULT_LABELS = {
    "mastered": "熟知",
    "known": "认识",
    "fuzzy": "模糊",
    "forgotten": "忘记",
    "skipped": "跳过",
}

SUCCESS_RESULTS = frozenset({"mastered", "known", "fuzzy"})
COGNITIVE_RESULTS = frozenset({"mastered", "known", "fuzzy", "forgotten"})


def clamp_stability(s: float) -> float:
    return max(MIN_S, min(MAX_S, float(s)))


def retention(days: float, stability: float) -> float:
    """R(t) = exp(-t / S)."""
    s = max(stability, 1e-9)
    t = max(0.0, float(days))
    return math.exp(-t / s)


def interval_for_stability(stability: float, target_r: float = TARGET_R) -> float:
    """Days until R(t) drops to target_r."""
    s = clamp_stability(stability)
    r = min(max(float(target_r), 1e-6), 0.999999)
    return -s * math.log(r)


def hold_days(stability: float, target_r: float = TARGET_R) -> float:
    """Predicted days the item stays above target retention (same as interval_for_stability)."""
    return interval_for_stability(stability, target_r)


def update_stability(stability: float, result: str) -> float:
    """Update S after a graded review. skipped leaves S unchanged (caller should skip)."""
    s = clamp_stability(stability if stability is not None else INITIAL_S)
    if result == "mastered":
        return clamp_stability(s * 2.5 + 0.5)
    if result == "known":
        return clamp_stability(s * 2.0)
    if result == "fuzzy":
        return clamp_stability(s * 1.2)
    if result == "forgotten":
        return INITIAL_S
    if result == "skipped":
        return s
    raise ValueError(f"unknown result: {result}")


def grade_attempt(status: str, attempt_n: int = 1, duration_ms: int = 0) -> str:
    """Map status + attempt metadata to a cognitive grade.

    See module docstring for the full mapping table.
    """
    status = (status or "").lower()
    if status == "skipped":
        return "skipped"
    if status == "wrong":
        return "forgotten"
    if status != "correct":
        raise ValueError(f"unknown status: {status}")
    n = max(1, int(attempt_n or 1))
    if n > 1:
        return "fuzzy"
    ms = int(duration_ms or 0)
    if ms > 0 and ms <= FAST_MS:
        return "mastered"
    return "known"


def fixed_interval_days(streak_after_correct: int) -> int:
    idx = max(0, min(streak_after_correct - 1, len(FIXED_INTERVALS) - 1))
    return FIXED_INTERVALS[idx]


def schedule_next(
    *,
    mode: str,
    result: str,
    stability_after: float,
    streak_after: int,
    now: datetime | None = None,
) -> tuple[str, float]:
    """Return (next_review_at_iso, interval_days).

    skipped: caller should not change due; this still returns current stamp + 0
    for bookkeeping if invoked.
    """
    now = now or datetime.now(timezone.utc)
    stamp = now.isoformat(timespec="seconds")
    mode = (mode or DEFAULT_SCHEDULER_MODE).lower()
    if result == "skipped":
        return stamp, 0.0
    if result == "forgotten":
        return stamp, 0.0

    if mode == "fixed":
        days = float(fixed_interval_days(streak_after))
        due = (now + timedelta(days=days)).isoformat(timespec="seconds")
        return due, days

    # dynamic
    days = interval_for_stability(stability_after)
    due = (now + timedelta(days=days)).isoformat(timespec="seconds")
    return due, days


def ebbinghaus_theory_rate(days: float) -> float:
    """Reference forgetting curve R(t)=exp(-t/S_REF) with t in days.

    Day 0 is 100% retention. S_REF is calibrated so day 1 lands near the
    classic ~33–44% band; the curve is smooth and strictly decreasing.
    """
    if days <= 0:
        return 1.0
    return retention(float(days), S_REF)


def blend_user_rate(theory_pct: float, empirical_pct: float | None, sample_size: int) -> float:
    """Blend empirical retention with theory prior.

    w = n/(n+K); when n=0 the result equals theory (user curve overlays reference).
    """
    n = max(0, int(sample_size or 0))
    theory = float(theory_pct)
    if n <= 0 or empirical_pct is None:
        return theory  # exact prior — do not re-round away from theory_curve_points
    w = n / (n + CURVE_BLEND_K)
    blended = w * float(empirical_pct) + (1.0 - w) * theory
    return round(max(0.0, min(100.0, blended)), 2)


def theory_curve_points(max_offset: int = 11) -> list[dict]:
    """Points for offsets 0..max_offset days with Chinese labels."""
    points = []
    for d in range(max_offset + 1):
        if d == 0:
            label = "今天"
        elif d == 1:
            label = "明天"
        elif d == 2:
            label = "后天"
        else:
            label = f"{d}天后"
        rate = ebbinghaus_theory_rate(d)
        # Keep two decimals so late-window values stay strictly ordered before flooring to 0.
        pct = round(rate * 100, 2)
        points.append({
            "offsetDays": d,
            "label": label,
            "theory": pct,
        })
    return points


def parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        text = value.replace("Z", "+00:00")
        dt = datetime.fromisoformat(text)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except (TypeError, ValueError):
        return None


def local_date(dt: datetime | None = None):
    """Calendar date in the server's local timezone."""
    if dt is None:
        return datetime.now().astimezone().date()
    return dt.astimezone().date()
