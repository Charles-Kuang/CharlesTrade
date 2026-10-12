"""Find calendar-day gaps not yet marked covered in the store."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone


def ensure_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def iter_utc_days(start: datetime, end: datetime):
    """Yield each UTC calendar day that overlaps half-open [start, end)."""
    start = ensure_utc(start)
    end = ensure_utc(end)
    if end <= start:
        return

    cursor = datetime(start.year, start.month, start.day, tzinfo=timezone.utc)
    while cursor < end:
        yield cursor.date()
        cursor += timedelta(days=1)


def day_window(day: date) -> tuple[datetime, datetime]:
    start = datetime(day.year, day.month, day.day, tzinfo=timezone.utc)
    end = start + timedelta(days=1)
    return start, end


def missing_days(
    start: datetime,
    end: datetime,
    covered: set[str],
) -> list[date]:
    """Return UTC days in [start, end) not present in covered (YYYY-MM-DD)."""
    return [day for day in iter_utc_days(start, end) if day.isoformat() not in covered]
