"""Timezone helpers used for display and natural-day statistics.

Review scheduling intentionally does not live here.  All scheduling is handled
by :mod:`fsrs_service`, and all scheduler timestamps remain in UTC.
"""
from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


def parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _resolve_zone(tz_name: str | None) -> ZoneInfo | None:
    if not tz_name:
        return None
    try:
        return ZoneInfo(tz_name)
    except (ZoneInfoNotFoundError, ValueError):
        return None


def is_valid_timezone(tz_name: str) -> bool:
    return _resolve_zone(tz_name) is not None


def local_date(dt: datetime | None = None, tz_name: str | None = None):
    zone = _resolve_zone(tz_name)
    dt = dt or datetime.now(timezone.utc)
    return dt.astimezone(zone).date() if zone else dt.astimezone().date()
