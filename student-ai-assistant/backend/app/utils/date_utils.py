"""
Date and timezone helpers.

Everything is stored in UTC and displayed in IST. The students are all in one
timezone, so the only real risk is a naive datetime silently being treated as
UTC when it was meant as local — every function here forces the question.
"""

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")
UTC = timezone.utc


def now_utc() -> datetime:
    return datetime.now(UTC)


def now_ist() -> datetime:
    return datetime.now(IST)


def ensure_aware(dt: datetime, assume: ZoneInfo | timezone = UTC) -> datetime:
    """Attach a timezone to a naive datetime rather than letting it drift."""
    return dt.replace(tzinfo=assume) if dt.tzinfo is None else dt


def to_ist(dt: datetime) -> datetime:
    return ensure_aware(dt).astimezone(IST)


def to_utc(dt: datetime) -> datetime:
    return ensure_aware(dt).astimezone(UTC)


def hours_until_exact(dt: datetime) -> float:
    """Fractional hours from now until `dt`. Negative once it has passed."""
    return (ensure_aware(dt) - now_utc()).total_seconds() / 3600


def hours_until(dt: datetime) -> int:
    """
    Hours remaining, rounded to nearest, for display.

    Rounded rather than floored: a deadline 4h59m away is "5h left" to a person,
    and flooring showed "4h". Comparisons against thresholds must use
    hours_until_exact — rounding a boundary value is how "25 hours away" ended
    up classified as due-within-24h.
    """
    return round(hours_until_exact(dt))


def days_until(dt: datetime) -> int:
    """
    Calendar days from today until `dt`, in IST.

    Calendar days, not 24-hour blocks: something due at 09:00 tomorrow is
    "tomorrow" to a student even when it is only 14 hours away. The old
    implementation used timedelta.days and reported that as 0 — "due today".
    """
    return (to_ist(dt).date() - now_ist().date()).days


def priority_from_deadline(dt: datetime) -> str:
    """Exact hours, not the rounded display value — see hours_until."""
    hours = hours_until_exact(dt)
    if hours <= 24:
        return "HIGH"
    if hours <= 72:
        return "MEDIUM"
    return "LOW"


def parse_classroom_date(due_date: dict, due_time: dict | None = None) -> datetime:
    """
    Convert a Google Classroom dueDate/dueTime pair to UTC.

    Classroom returns these as UTC components, not local ones — the API
    documents dueTime as UTC. Treating them as IST (as the previous version did)
    shifted every Classroom deadline 5h30m early, which for a 23:59 due time
    lands it on the wrong calendar day.

    A missing dueTime means "end of day" in the course's timezone; 23:59 IST is
    the safe reading, since erring late risks a missed submission.
    """
    year = due_date.get("year")
    month = due_date.get("month")
    day = due_date.get("day")
    if not (year and month and day):
        raise ValueError(f"incomplete Classroom dueDate: {due_date!r}")

    if due_time and ("hours" in due_time or "minutes" in due_time):
        return datetime(
            year, month, day,
            due_time.get("hours", 0),
            due_time.get("minutes", 0),
            tzinfo=UTC,
        )

    # Date with no time: end of that day, IST.
    return datetime(year, month, day, 23, 59, tzinfo=IST).astimezone(UTC)


def format_deadline_for_telegram(dt: datetime) -> str:
    """Human-friendly deadline string, IST."""
    ist = to_ist(dt)
    hours = hours_until_exact(dt)
    days = days_until(dt)
    clock = ist.strftime("%I:%M %p").lstrip("0")

    if hours < 0:
        overdue = abs(int(hours))
        return f"overdue by {overdue}h" if overdue < 48 else f"overdue by {overdue // 24}d"
    if days == 0:
        return f"today at {clock} ({hours_until(dt)}h left)"
    if days == 1:
        return f"tomorrow at {clock}"
    return f"{ist.strftime('%a %d %b')} at {clock} ({days}d left)"


def humanize_time_left(dt: datetime) -> str:
    """Compact form for dense lists: '6h', '2d', 'overdue'."""
    hours = hours_until_exact(dt)
    if hours < 0:
        return "overdue"
    if hours < 24:
        return f"{hours_until(dt)}h"
    return f"{days_until(dt)}d"


def start_of_day_ist(dt: datetime | None = None) -> datetime:
    ist = to_ist(dt) if dt else now_ist()
    return ist.replace(hour=0, minute=0, second=0, microsecond=0)


def parse_iso(value: str | None) -> datetime | None:
    """Lenient ISO-8601 parse: returns None instead of raising."""
    if not value:
        return None
    try:
        return ensure_aware(datetime.fromisoformat(value.replace("Z", "+00:00")))
    except (ValueError, AttributeError):
        return None
