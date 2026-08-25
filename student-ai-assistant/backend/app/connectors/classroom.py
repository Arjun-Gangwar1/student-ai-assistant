"""
Google Classroom connector — the primary deadline source.

Scopes (all SENSITIVE, no CASA assessment required):
  classroom.courses.readonly
  classroom.student-submissions.me.readonly
  classroom.announcements.readonly

Classroom deadlines are authoritative: they come from the API as structured
fields, not from an LLM reading prose, so they are stored confirmed.
"""

import logging
from datetime import timedelta

from starlette.concurrency import run_in_threadpool

from app.connectors.google_auth import GoogleAuthError, build_service, has_scope
from app.db import queries
from app.utils.date_utils import now_utc, parse_classroom_date, parse_iso

logger = logging.getLogger(__name__)

MAX_ANNOUNCEMENTS_PER_COURSE = 20
# ACTIVE only: archived courses are last semester's, and their deadlines are all
# in the past. Fetching them wastes quota and pollutes the radar.
COURSE_STATES = ["ACTIVE"]

# ACTIVE alone is not enough: IITDh does not appear to archive courses when a
# semester ends, so "MA 101 2024" is still returned as ACTIVE two years later.
# A course with no coursework or announcement activity in this window is
# treated as a finished semester Google was never told to close out.
COURSE_STALE_AFTER = timedelta(days=270)


async def sync_student_classroom(student: dict) -> dict:
    """
    Pull courses, coursework and announcements.
    Returns counts; never raises for a single course's failure.
    """
    student_id = str(student["id"])
    counts = {
        "courses": 0, "assignments": 0, "announcements": 0,
        "errors": 0, "stale_courses_skipped": 0,
    }

    if not has_scope(student, "classroom"):
        logger.info("Classroom scope not granted for %s — skipping", student_id)
        return counts

    try:
        service = await build_service(student, "classroom", "v1")
    except GoogleAuthError as exc:
        logger.warning("Classroom auth failed for %s: %s", student_id, exc)
        counts["errors"] += 1
        return counts

    try:
        response = await run_in_threadpool(
            lambda: service.courses()
            .list(studentId="me", courseStates=COURSE_STATES, pageSize=50)
            .execute()
        )
        courses = response.get("courses", [])
    except Exception as exc:
        logger.error("Course list failed for %s: %s", student_id, exc)
        counts["errors"] += 1
        return counts

    counts["courses"] = len(courses)

    for course in courses:
        course_id = course["id"]
        course_name = course.get("name", "Unknown course")

        coursework = await _fetch_coursework(service, course_id, course_name, counts)
        announcements = await _fetch_announcements(service, course_id, course_name, counts)

        if not _course_is_current(coursework, announcements):
            logger.info(
                "Skipping stale course %r (%s) — no activity in %d days",
                course_name, course_id, COURSE_STALE_AFTER.days,
            )
            counts["stale_courses_skipped"] += 1
            continue

        counts["assignments"] += await _save_coursework(
            student_id, course_id, course_name, coursework, counts
        )
        counts["announcements"] += await _save_announcements(
            student_id, course_id, course_name, announcements, counts
        )

    logger.info("Classroom sync for %s: %s", student_id, counts)
    return counts


def _course_is_current(coursework: list[dict], announcements: list[dict]) -> bool:
    """
    True if this course has any coursework or announcement activity within
    COURSE_STALE_AFTER, or has none at all yet (a brand-new course with
    nothing posted should not be treated as finished).
    """
    if not coursework and not announcements:
        return True

    cutoff = now_utc() - COURSE_STALE_AFTER

    for work in coursework:
        if due_date := work.get("dueDate"):
            try:
                if parse_classroom_date(due_date, work.get("dueTime")) >= cutoff:
                    return True
            except ValueError:
                pass
        if (created := parse_iso(work.get("creationTime"))) and created >= cutoff:
            return True

    for announcement in announcements:
        if (updated := parse_iso(announcement.get("updateTime"))) and updated >= cutoff:
            return True

    return False


async def _fetch_coursework(service, course_id, course_name, counts) -> list[dict]:
    try:
        response = await run_in_threadpool(
            lambda: service.courses()
            .courseWork()
            .list(courseId=course_id, orderBy="dueDate desc", pageSize=50)
            .execute()
        )
    except Exception as exc:
        logger.warning("Coursework fetch failed (%s): %s", course_name, exc)
        counts["errors"] += 1
        return []
    return response.get("courseWork", [])


async def _save_coursework(student_id, course_id, course_name, coursework, counts) -> int:
    saved = 0
    for work in coursework:
        title = work.get("title", "Untitled")
        description = work.get("description", "")

        due_at = None
        if due_date := work.get("dueDate"):
            try:
                due_at = parse_classroom_date(due_date, work.get("dueTime"))
            except ValueError as exc:
                logger.debug("Unusable dueDate on %r: %s", title, exc)

        try:
            item = await queries.upsert_item(
                student_id=student_id,
                source="classroom",
                source_id=work["id"],
                raw_content=f"[{course_name}] {title}\n{description}".strip(),
                title=f"{course_name}: {title}",
                deadline=due_at,
                metadata={
                    "course_id": course_id,
                    "course_name": course_name,
                    "work_type": work.get("workType"),
                    "max_points": work.get("maxPoints"),
                    "link": work.get("alternateLink"),
                },
            )

            if due_at:
                await queries.upsert_deadline(
                    student_id=student_id,
                    # Keyed on the Classroom id, so re-syncing updates the row
                    # rather than inserting a duplicate with fresh alert flags.
                    dedup_key=f"classroom:{work['id']}",
                    item_id=str(item["id"]),
                    title=f"{course_name}: {title}",
                    due_at=due_at,
                    source="classroom",
                    confirmed=True,      # structured API field, not an inference
                    confidence=1.0,
                )
                saved += 1
        except Exception as exc:
            logger.error("Saving coursework %r failed: %s", title, exc)
            counts["errors"] += 1

    return saved


async def _fetch_announcements(service, course_id, course_name, counts) -> list[dict]:
    try:
        response = await run_in_threadpool(
            lambda: service.courses()
            .announcements()
            .list(courseId=course_id, orderBy="updateTime desc",
                  pageSize=MAX_ANNOUNCEMENTS_PER_COURSE)
            .execute()
        )
    except Exception as exc:
        logger.warning("Announcements fetch failed (%s): %s", course_name, exc)
        counts["errors"] += 1
        return []
    return response.get("announcements", [])


async def _save_announcements(student_id, course_id, course_name, announcements, counts) -> int:
    saved = 0
    for announcement in announcements:
        text = (announcement.get("text") or "").strip()
        if not text:
            continue
        try:
            await queries.upsert_item(
                student_id=student_id,
                source="classroom",
                source_id=announcement["id"],
                raw_content=f"[{course_name}] {text}",
                # First line as the title: an announcement has no title field,
                # and "Announcement: Physics" for every one of them is useless
                # in a list.
                title=f"{course_name}: {text.splitlines()[0][:120]}",
                metadata={
                    "course_id": course_id,
                    "course_name": course_name,
                    "type": "announcement",
                    "update_time": announcement.get("updateTime"),
                    "link": announcement.get("alternateLink"),
                },
            )
            saved += 1
        except Exception as exc:
            logger.error("Saving announcement failed (%s): %s", course_name, exc)
            counts["errors"] += 1

    return saved
