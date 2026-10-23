"""Deadline / timestamp parsing. Stored values are UTC; display is Europe/Istanbul."""
from __future__ import annotations

import re
from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

_DATE_ONLY = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def local_tz():
    try:
        return ZoneInfo("Europe/Istanbul")
    except ZoneInfoNotFoundError:  # no tzdata installed; Istanbul has been fixed UTC+3 since 2016
        return timezone(timedelta(hours=3), "Europe/Istanbul")


def parse_when(value: str, date_only_at: time) -> datetime:
    """Parse ISO 8601 into an aware UTC datetime.

    Date-only values mean `date_only_at` in Europe/Istanbul; naive datetimes are Istanbul local time.
    Raises ValueError for anything else.
    """
    value = value.strip()
    if _DATE_ONLY.match(value):
        dt = datetime.combine(datetime.fromisoformat(value).date(), date_only_at, tzinfo=local_tz())
    else:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=local_tz())
    return dt.astimezone(timezone.utc)


def parse_due(value: str) -> datetime:
    """A bare date is a deadline at the *end* of that day (23:59:59 Istanbul)."""
    return parse_when(value, time(23, 59, 59))


def parse_instant(value: str) -> datetime:
    """A bare date is the *start* of that day (00:00:00 Istanbul)."""
    return parse_when(value, time(0, 0, 0))


def iso_utc(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds")


def to_local(dt: datetime) -> str:
    return dt.astimezone(local_tz()).isoformat(timespec="seconds")


def safe_parse_utc(value: str | None) -> datetime | None:
    """Tolerant read of stored values (Sprint-0 rows kept the user's raw due string)."""
    if not value:
        return None
    try:
        return parse_due(value)
    except ValueError:
        return None
