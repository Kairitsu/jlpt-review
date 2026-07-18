"""Timezone helpers used for display and natural-day statistics.

Review scheduling intentionally does not live here.  All scheduling is handled
by :mod:`fsrs_service`, and all scheduler timestamps remain in UTC.
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
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


def local_day_utc_bounds(
    dt: datetime | None = None,
    tz_name: str | None = None,
) -> tuple[datetime, datetime]:
    """Return the UTC half-open interval for ``dt``'s local calendar day.

    An explicit IANA timezone uses :class:`ZoneInfo`.  With no configured
    timezone, naive local midnights are resolved by the server process so this
    follows the same server-timezone fallback as :func:`local_date`, including
    daylight-saving changes.
    """
    dt = dt or datetime.now(timezone.utc)
    zone = _resolve_zone(tz_name)
    day = dt.astimezone(zone).date() if zone else dt.astimezone().date()

    return local_date_utc_bounds(day, tz_name=tz_name)


def local_date_utc_bounds(
    day: date,
    tz_name: str | None = None,
) -> tuple[datetime, datetime]:
    """Return the UTC half-open interval for one local calendar date."""
    zone = _resolve_zone(tz_name)

    def local_midnight(value):
        if zone:
            return datetime.combine(value, time.min, tzinfo=zone)
        return datetime.combine(value, time.min).astimezone()

    start = local_midnight(day).astimezone(timezone.utc)
    end = local_midnight(day + timedelta(days=1)).astimezone(timezone.utc)
    return start, end
