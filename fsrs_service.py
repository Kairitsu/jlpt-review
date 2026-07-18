"""The application's only automatic-rating and review-scheduler integration.

This module is deliberately a thin persistence boundary around the official
``fsrs`` package.  No FSRS equations are duplicated in application code.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Iterable, Mapping, Sequence

from fsrs import Card, Rating, ReviewLog, Scheduler, State

FSRS_VERSION = "6.3.1"
RATING_POLICY_VERSION = 2
DESIRED_RETENTION = 0.90
MAXIMUM_INTERVAL_DAYS = 36500
LEARNING_STEPS = ()
RELEARNING_STEPS = ()

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
        learning_steps=LEARNING_STEPS,
        relearning_steps=RELEARNING_STEPS,
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


@dataclass(frozen=True)
class AttemptFacts:
    """Auditable facts from the real check actions in one session item."""

    attempt_count: int
    first_attempt_correct: bool | None
    second_attempt_correct: bool | None
    final_attempt_correct: bool | None


def attempt_facts(attempts: Iterable[Mapping]) -> AttemptFacts:
    """Extract ordered check facts while ignoring legacy skip records."""
    checks = [
        row for row in attempts if row["status"] in {"correct", "wrong"}
    ]
    if not checks:
        return AttemptFacts(0, None, None, None)
    correctness = [row["status"] == "correct" for row in checks]
    return AttemptFacts(
        attempt_count=len(correctness),
        first_attempt_correct=correctness[0],
        second_attempt_correct=correctness[1] if len(correctness) > 1 else None,
        final_attempt_correct=correctness[-1],
    )


def determine_fsrs_rating(
    attempts: Iterable[Mapping],
    *,
    previous_first_attempt_correct: bool | None,
) -> Rating | None:
    """Return the sole automatic rating for one finalized practice item.

    Only real check order and the previous reliable first-check fact matter.
    Duration, client-provided ratings, and checks after the second one cannot
    change the result.
    """
    facts = attempt_facts(attempts)
    if facts.attempt_count == 0:
        return None
    if facts.first_attempt_correct:
        return (
            Rating.Easy
            if previous_first_attempt_correct is True
            else Rating.Good
        )
    if facts.second_attempt_correct is True:
        return Rating.Hard
    return Rating.Again


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


def reschedule_from_review_events(
    row: Mapping,
    events: Sequence[Mapping],
    *,
    enable_fuzzing: bool = True,
) -> dict:
    """Replay persisted audit events with the current official scheduler.

    ``review_events`` remains immutable during migration.  This adapter only
    converts those rows to the package's ``ReviewLog`` type and delegates the
    full state calculation to ``Scheduler.reschedule_card()``.
    """
    logs = sorted(
        (
            ReviewLog(
                card_id=int(row["id"]),
                rating=Rating(int(event["rating"])),
                review_datetime=parse_utc(event["reviewed_at"]),
                review_duration=max(0, int(event["duration_ms"] or 0)),
            )
            for event in events
        ),
        key=lambda log: log.review_datetime,
    )
    if not logs:
        raise ValueError("at least one review event is required for rescheduling")

    initial_due = parse_utc(row.get("created_at")) or logs[0].review_datetime
    if initial_due > logs[0].review_datetime:
        initial_due = logs[0].review_datetime
    rescheduled = scheduler(enable_fuzzing=enable_fuzzing).reschedule_card(
        Card(card_id=int(row["id"]), due=initial_due),
        logs,
    )
    return card_fields(rescheduled)


def retrievability(row: Mapping, now: datetime | None = None) -> float:
    card = card_from_row(row)
    return float(scheduler(enable_fuzzing=False).get_card_retrievability(
        card, (now or utc_now()).astimezone(timezone.utc)
    ))
