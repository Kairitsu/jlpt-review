from datetime import datetime, timezone

import pytest
from fsrs import Rating, State

from fsrs_service import (
    FSRS_VERSION,
    card_fields,
    new_card,
    rating_from_attempts,
    review,
)
from memory import is_valid_timezone, local_date, parse_iso


NOW = datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc)


def row_for(card):
    return {"id": card.card_id, **card_fields(card)}


def test_new_card_is_due_fsrs_learning_card():
    card = new_card(42, NOW)
    fields = card_fields(card)
    assert card.state is State.Learning
    assert fields == {
        "fsrs_state": int(State.Learning),
        "fsrs_step": 0,
        "stability": None,
        "difficulty": None,
        "last_review_at": None,
        "next_review_at": NOW.isoformat(timespec="seconds"),
        "fsrs_version": FSRS_VERSION,
    }


@pytest.mark.parametrize(
    "attempts,easy,expected",
    [
        ([{"status": "skipped"}], False, None),
        ([{"status": "wrong"}], False, Rating.Again),
        ([{"status": "wrong"}, {"status": "correct"}], False, Rating.Hard),
        ([{"status": "correct"}], False, Rating.Good),
        ([{"status": "correct"}], True, Rating.Easy),
    ],
)
def test_rating_mapping(attempts, easy, expected):
    assert rating_from_attempts(attempts, easy=easy) == expected


@pytest.mark.parametrize("rating", list(Rating))
def test_official_fsrs_new_card_ratings_are_persistable(rating):
    outcome = review(
        row_for(new_card(7, NOW)), rating,
        reviewed_at=NOW, duration_ms=1200, enable_fuzzing=False,
    )
    assert outcome.rating is rating
    assert outcome.after["stability"] is not None
    assert outcome.after["difficulty"] is not None
    assert parse_iso(outcome.after["last_review_at"]) == NOW
    assert parse_iso(outcome.after["next_review_at"]) >= NOW
    assert outcome.after["fsrs_version"] == FSRS_VERSION


def test_timezone_helpers_do_not_change_utc_instant():
    stamp = "2026-01-01T16:30:00+00:00"
    parsed = parse_iso(stamp)
    assert parsed.tzinfo is timezone.utc
    assert local_date(parsed, "Asia/Shanghai").isoformat() == "2026-01-02"
    assert local_date(parsed, "UTC").isoformat() == "2026-01-01"
    assert is_valid_timezone("Asia/Shanghai")
    assert not is_valid_timezone("Mars/Olympus")
