"""The application's only review scheduler integration.

This module is deliberately a thin persistence boundary around the official
``fsrs`` package.  No FSRS equations are duplicated in application code.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Iterable, Mapping

from fsrs import Card, Rating, Scheduler, State

FSRS_VERSION = "6.3.1"
DESIRED_RETENTION = 0.90
MAXIMUM_INTERVAL_DAYS = 36500

RATING_NAMES = {
    Rating.Again: "again",
    Rating.Hard: "hard",
    Rating.Good: "good",
    Rating.Easy: "easy",
}
RATING_LABELS_ZH = {
    Rating.Again: "忘记",
    Rating.Hard: "模糊",
    Rating.Good: "认识",
    Rating.Easy: "轻松掌握",
}


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def utc_iso(value: datetime) -> str:
    if value.tzinfo is None:
        raise ValueError("FSRS datetime must be timezone-aware")
    return value.astimezone(timezone.utc).isoformat(timespec="seconds")


def parse_utc(value: str | None) -> datetime | None:
    if not value:
        return None
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def scheduler(*, enable_fuzzing: bool = True) -> Scheduler:
    return Scheduler(
        desired_retention=DESIRED_RETENTION,
        maximum_interval=MAXIMUM_INTERVAL_DAYS,
        enable_fuzzing=enable_fuzzing,
    )


def new_card(sentence_id: int, now: datetime | None = None) -> Card:
    due = (now or utc_now()).astimezone(timezone.utc)
    return Card(card_id=sentence_id, due=due)


def card_from_row(row: Mapping) -> Card:
    return Card(
        card_id=int(row["id"]),
        state=State(int(row["fsrs_state"])),
        step=row["fsrs_step"],
        stability=row["stability"],
        difficulty=row["difficulty"],
        due=parse_utc(row["next_review_at"]),
        last_review=parse_utc(row["last_review_at"]),
    )


def card_fields(card: Card) -> dict:
    return {
        "fsrs_state": int(card.state),
        "fsrs_step": card.step,
        "stability": card.stability,
        "difficulty": card.difficulty,
        "last_review_at": utc_iso(card.last_review) if card.last_review else None,
        "next_review_at": utc_iso(card.due),
        "fsrs_version": FSRS_VERSION,
    }


def rating_from_attempts(attempts: Iterable[Mapping], *, easy: bool = False) -> Rating | None:
    """Map one question's complete raw attempt history to a final FSRS rating.

    A final skip is not reviewed.  Otherwise a question ending without a
    correct answer is Again, a recovery after any wrong check is Hard, and a
    first-check success is Good or Easy according to the explicit user choice.
    """
    rows = list(attempts)
    if not rows or rows[-1]["status"] == "skipped":
        return None
    checks = [row for row in rows if row["status"] != "skipped"]
    if not checks or checks[-1]["status"] != "correct":
        return Rating.Again
    if any(row["status"] == "wrong" for row in checks[:-1]):
        return Rating.Hard
    return Rating.Easy if easy else Rating.Good


@dataclass(frozen=True)
class ReviewResult:
    rating: Rating
    before: dict
    after: dict
    duration_ms: int


def review(
    row: Mapping,
    rating: Rating,
    *,
    reviewed_at: datetime | None = None,
    duration_ms: int = 0,
    enable_fuzzing: bool = True,
) -> ReviewResult:
    when = (reviewed_at or utc_now()).astimezone(timezone.utc)
    before_card = card_from_row(row)
    before = card_fields(before_card)
    after_card, _ = scheduler(enable_fuzzing=enable_fuzzing).review_card(
        before_card,
        rating,
        review_datetime=when,
        review_duration=max(0, int(duration_ms)),
    )
    return ReviewResult(
        rating=rating,
        before=before,
        after=card_fields(after_card),
        duration_ms=max(0, int(duration_ms)),
    )


def retrievability(row: Mapping, now: datetime | None = None) -> float:
    card = card_from_row(row)
    return float(scheduler(enable_fuzzing=False).get_card_retrievability(
        card, (now or utc_now()).astimezone(timezone.utc)
    ))
