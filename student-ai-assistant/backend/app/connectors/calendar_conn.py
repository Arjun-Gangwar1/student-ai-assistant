"""
Google Calendar connector.
Scope used (SENSITIVE — no CASA):
  - https://www.googleapis.com/auth/calendar.events.readonly
"""

import logging
from datetime import datetime, timezone, timedelta
from googleapiclient.discovery import build
from google.oauth2.credentials import Credentials
from tenacity import retry, stop_after_attempt, wait_exponential

from app.db.supabase import upsert_item, upsert_deadline

logger = logging.getLogger(__name__)

CALENDAR_SCOPES = [
    "https://www.googleapis.com/auth/calendar.events.readonly",
]


def build_service(token_data: dict):
    creds = Credentials(
        token=token_data.get("access_token"),
        refresh_token=token_data.get("refresh_token"),
        token_uri="https://oauth2.googleapis.com/token",
        client_id=token_data.get("client_id"),
        client_secret=token_data.get("client_secret"),
        scopes=CALENDAR_SCOPES,
    )
    return build("calendar", "v3", credentials=creds, cache_discovery=False)


def _parse_event_datetime(dt_obj: dict) -> datetime | None:
    """Parse Google Calendar event dateTime or date field."""
    if not dt_obj:
        return None
    raw = dt_obj.get("dateTime") or dt_obj.get("date")
    if not raw:
        return None
    try:
        if "T" in raw:
            dt = datetime.fromisoformat(raw)
        else:
            dt = datetime.fromisoformat(raw + "T00:00:00+05:30")  # IST midnight
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except ValueError:
        return None


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10))
async def sync_student_calendar(student: dict, lookahead_days: int = 30) -> int:
    """
    Fetch upcoming calendar events for a student.
    Returns count of items upserted.
    """
    tokens = student.get("google_tokens")
    if not tokens:
        return 0

    service = build_service(tokens)
    student_id = student["id"]

    now = datetime.now(timezone.utc)
    time_max = now + timedelta(days=lookahead_days)
    count = 0

    try:
        events_resp = (
            service.events()
            .list(
                calendarId="primary",
                timeMin=now.isoformat(),
                timeMax=time_max.isoformat(),
                singleEvents=True,
                orderBy="startTime",
                maxResults=100,
            )
            .execute()
        )
    except Exception as e:
        logger.error(f"Calendar fetch failed for {student_id}: {e}")
        return 0

    for event in events_resp.get("items", []):
        start = _parse_event_datetime(event.get("start", {}))
        if not start:
            continue

        summary = event.get("summary", "Untitled Event")
        description = event.get("description", "")
        raw = f"{summary}\n{description}".strip()

        item_data = {
            "student_id": student_id,
            "source": "calendar",
            "source_id": event["id"],
            "raw_content": raw,
            "title": summary,
            "deadline": start.isoformat(),
            "metadata": {
                "location": event.get("location"),
                "html_link": event.get("htmlLink"),
                "event_type": event.get("eventType", "default"),
            },
        }

        saved_item = await upsert_item(item_data)

        if saved_item:
            await upsert_deadline({
                "student_id": student_id,
                "item_id": saved_item["id"],
                "title": summary,
                "due_at": start.isoformat(),
                "source": "calendar",
                "confirmed": True,
                "calendar_event_id": event["id"],
            })
            count += 1

    logger.info(f"Calendar sync done for {student_id}: {count} events")
    return count
