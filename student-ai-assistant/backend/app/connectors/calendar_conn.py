"""
Google Calendar connector.

Scope: calendar.events.readonly (SENSITIVE — no CASA assessment).
"""

import logging
from datetime import datetime, timedelta

from starlette.concurrency import run_in_threadpool

from app.connectors.google_auth import GoogleAuthError, build_service, has_scope
from app.db import queries
from app.utils.date_utils import IST, UTC, now_utc

logger = logging.getLogger(__name__)

LOOKAHEAD_DAYS = 60
MAX_EVENTS = 250

# All-day events and multi-day blocks are context, not deadlines. Creating a
# "deadline" for a week-long fest produces an alert at 05:59 on a day nothing is
# actually due.
SKIP_DEADLINE_EVENT_TYPES = {"outOfOffice", "focusTime", "workingLocation", "birthday"}


def _parse_event_time(node: dict) -> tuple[datetime | None, bool]:
    """
    Parse a Calendar start/end node.
    Returns (datetime in UTC, is_all_day).
    """
    if not node:
        return None, False

    if raw := node.get("dateTime"):
        try:
            parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            return parsed.astimezone(UTC), False
        except ValueError:
            return None, False

    if raw := node.get("date"):
        # A bare date is a local all-day event. Anchor to IST midnight so it
        # lands on the day the student sees in their calendar.
        try:
            parsed = datetime.fromisoformat(raw).replace(tzinfo=IST)
            return parsed.astimezone(UTC), True
        except ValueError:
            return None, False

    return None, False


async def sync_student_calendar(student: dict, lookahead_days: int = LOOKAHEAD_DAYS) -> dict:
    student_id = str(student["id"])
    counts = {"events": 0, "deadlines": 0, "errors": 0}

    if not has_scope(student, "calendar"):
        logger.info("Calendar scope not granted for %s — skipping", student_id)
        return counts

    try:
        service = await build_service(student, "calendar", "v3")
    except GoogleAuthError as exc:
        logger.warning("Calendar auth failed for %s: %s", student_id, exc)
        counts["errors"] += 1
        return counts

    now = now_utc()
    time_max = now + timedelta(days=lookahead_days)

    try:
        response = await run_in_threadpool(
            lambda: service.events()
            .list(
                calendarId="primary",
                timeMin=now.isoformat(),
                timeMax=time_max.isoformat(),
                singleEvents=True,      # expand recurring series into instances
                orderBy="startTime",
                maxResults=MAX_EVENTS,
            )
            .execute()
        )
    except Exception as exc:
        logger.error("Calendar fetch failed for %s: %s", student_id, exc)
        counts["errors"] += 1
        return counts

    for event in response.get("items", []):
        if event.get("status") == "cancelled":
            continue

        start, is_all_day = _parse_event_time(event.get("start", {}))
        if start is None:
            continue

        summary = event.get("summary") or "Untitled event"
        description = event.get("description") or ""
        location = event.get("location") or ""
        event_type = event.get("eventType", "default")

        # A declined invitation is not the student's commitment.
        attendee_self = next(
            (a for a in event.get("attendees", []) if a.get("self")), None
        )
        if attendee_self and attendee_self.get("responseStatus") == "declined":
            continue

        try:
            item = await queries.upsert_item(
                student_id=student_id,
                source="calendar",
                source_id=event["id"],
                raw_content="\n".join(filter(None, [summary, description, location])),
                title=summary,
                deadline=start,
                metadata={
                    "location": location,
                    "link": event.get("htmlLink"),
                    "event_type": event_type,
                    "all_day": is_all_day,
                    "organizer": (event.get("organizer") or {}).get("email"),
                },
            )
            counts["events"] += 1

            if not is_all_day and event_type not in SKIP_DEADLINE_EVENT_TYPES:
                await queries.upsert_deadline(
                    student_id=student_id,
                    dedup_key=f"calendar:{event['id']}",
                    item_id=str(item["id"]),
                    title=summary,
                    due_at=start,
                    source="calendar",
                    confirmed=True,     # the student put it there themselves
                    confidence=1.0,
                    calendar_event_id=event["id"],
                )
                counts["deadlines"] += 1
        except Exception as exc:
            logger.error("Saving calendar event %r failed: %s", summary, exc)
            counts["errors"] += 1

    logger.info("Calendar sync for %s: %s", student_id, counts)
    return counts
