from datetime import datetime, timezone, timedelta
import pytz

IST = pytz.timezone("Asia/Kolkata")


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def to_ist(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(IST)


def days_until(dt: datetime) -> int:
    now = now_utc()
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    delta = dt - now
    return max(0, delta.days)


def hours_until(dt: datetime) -> int:
    now = now_utc()
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    delta = dt - now
    return max(0, int(delta.total_seconds() // 3600))


def priority_from_deadline(dt: datetime) -> str:
    h = hours_until(dt)
    if h <= 24:
        return "HIGH"
    if h <= 72:
        return "MEDIUM"
    return "LOW"


def parse_classroom_date(due_date: dict, due_time: dict | None = None) -> datetime:
    """Convert Google Classroom dueDate + dueTime dict to UTC datetime."""
    year  = due_date.get("year", 2026)
    month = due_date.get("month", 1)
    day   = due_date.get("day", 1)
    hour  = due_time.get("hours", 23) if due_time else 23
    minute = due_time.get("minutes", 59) if due_time else 59

    # Classroom dates are in IST
    dt_ist = IST.localize(datetime(year, month, day, hour, minute))
    return dt_ist.astimezone(timezone.utc)


def format_deadline_for_telegram(dt: datetime) -> str:
    """Human-friendly deadline string for Telegram messages."""
    ist_dt = to_ist(dt)
    h = hours_until(dt)
    if h < 24:
        return f"today at {ist_dt.strftime('%I:%M %p')} ({h}h left)"
    d = days_until(dt)
    if d == 1:
        return f"tomorrow at {ist_dt.strftime('%I:%M %p')}"
    return f"{ist_dt.strftime('%d %b')} at {ist_dt.strftime('%I:%M %p')} ({d}d left)"
