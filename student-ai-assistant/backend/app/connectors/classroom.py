"""
Google Classroom connector.
Scopes used (SENSITIVE only — no CASA audit required):
  - https://www.googleapis.com/auth/classroom.courses.readonly
  - https://www.googleapis.com/auth/classroom.coursework.me.readonly
  - https://www.googleapis.com/auth/classroom.announcements.readonly
"""

import logging
from datetime import datetime, timezone
from googleapiclient.discovery import build
from google.oauth2.credentials import Credentials
from tenacity import retry, stop_after_attempt, wait_exponential

from app.db.supabase import upsert_item, upsert_deadline
from app.utils.date_utils import parse_classroom_date

logger = logging.getLogger(__name__)

CLASSROOM_SCOPES = [
    "https://www.googleapis.com/auth/classroom.courses.readonly",
    "https://www.googleapis.com/auth/classroom.student-submissions.me.readonly",
    "https://www.googleapis.com/auth/classroom.announcements.readonly",
]


def build_service(token_data: dict):
    creds = Credentials(
        token=token_data.get("access_token"),
        refresh_token=token_data.get("refresh_token"),
        token_uri="https://oauth2.googleapis.com/token",
        client_id=token_data.get("client_id"),
        client_secret=token_data.get("client_secret"),
        scopes=CLASSROOM_SCOPES,
    )
    return build("classroom", "v1", credentials=creds, cache_discovery=False)


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10))
async def sync_student_classroom(student: dict) -> dict:
    """
    Fetch all courses, coursework and announcements for a student.
    Returns counts of items upserted.
    """
    tokens = student.get("google_tokens")
    if not tokens:
        logger.warning(f"No tokens for student {student['id']}")
        return {"assignments": 0, "announcements": 0}

    service = build_service(tokens)
    student_id = student["id"]
    counts = {"assignments": 0, "announcements": 0}

    try:
        courses_resp = service.courses().list(studentId="me").execute()
        courses = courses_resp.get("courses", [])
    except Exception as e:
        logger.error(f"Failed to fetch courses for {student_id}: {e}")
        return counts

    for course in courses:
        course_id = course["id"]
        course_name = course.get("name", "Unknown Course")

        # ── Coursework (assignments) ────────────────────────────────────────
        try:
            cw_resp = (
                service.courses()
                .courseWork()
                .list(courseId=course_id, orderBy="dueDate asc")
                .execute()
            )
            for work in cw_resp.get("courseWork", []):
                raw = (
                    f"[{course_name}] {work.get('title', '')}\n"
                    f"{work.get('description', '')}"
                )
                item_data = {
                    "student_id": student_id,
                    "source": "classroom",
                    "source_id": work["id"],
                    "raw_content": raw,
                    "title": f"{course_name}: {work.get('title', '')}",
                    "metadata": {
                        "course_id": course_id,
                        "course_name": course_name,
                        "work_type": work.get("workType"),
                        "max_points": work.get("maxPoints"),
                    },
                }

                # Extract deadline if present
                due_date = work.get("dueDate")
                due_time = work.get("dueTime")
                if due_date:
                    due_at = parse_classroom_date(due_date, due_time)
                    item_data["deadline"] = due_at.isoformat()

                saved_item = await upsert_item(item_data)

                # Upsert into deadlines table if due date exists
                if due_date and saved_item:
                    await upsert_deadline({
                        "student_id": student_id,
                        "item_id": saved_item["id"],
                        "title": item_data["title"],
                        "due_at": item_data["deadline"],
                        "source": "classroom",
                        "confirmed": True,  # Classroom deadlines are authoritative
                    })
                    counts["assignments"] += 1

        except Exception as e:
            logger.error(f"Coursework fetch failed for course {course_id}: {e}")

        # ── Announcements ───────────────────────────────────────────────────
        try:
            ann_resp = (
                service.courses()
                .announcements()
                .list(courseId=course_id, orderBy="updateTime desc", pageSize=20)
                .execute()
            )
            for ann in ann_resp.get("announcements", []):
                raw = f"[{course_name}] {ann.get('text', '')}"
                await upsert_item({
                    "student_id": student_id,
                    "source": "classroom",
                    "source_id": ann["id"],
                    "raw_content": raw,
                    "title": f"Announcement: {course_name}",
                    "metadata": {
                        "course_id": course_id,
                        "course_name": course_name,
                        "update_time": ann.get("updateTime"),
                    },
                })
                counts["announcements"] += 1
        except Exception as e:
            logger.error(f"Announcements fetch failed for course {course_id}: {e}")

    logger.info(f"Classroom sync done for {student_id}: {counts}")
    return counts
