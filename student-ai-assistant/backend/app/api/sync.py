"""
Manual sync trigger endpoints — for development / first-run use.
"""

import logging
from fastapi import APIRouter, Request, HTTPException

from app.db.supabase import get_all_students, get_student_by_google_id
from app.connectors.classroom import sync_student_classroom
from app.connectors.calendar_conn import sync_student_calendar
from app.connectors.gmail_conn import sync_student_gmail
from app.intelligence.pipeline import process_student_items

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/sync", tags=["sync"])


@router.post("/now")
async def sync_now(request: Request):
    """Trigger full sync for the logged-in student immediately."""
    student_id = request.session.get("student_id")
    if not student_id:
        raise HTTPException(status_code=401, detail="Not authenticated")

    students = await get_all_students()
    student = next((s for s in students if s["id"] == student_id), None)
    if not student:
        raise HTTPException(status_code=404, detail="Student not found")

    results = {}

    # Gmail
    try:
        gmail_count = await sync_student_gmail(student)
        results["gmail"] = gmail_count
    except Exception as e:
        results["gmail_error"] = str(e)

    # Classroom
    try:
        classroom_counts = await sync_student_classroom(student)
        results["classroom"] = classroom_counts
        logger.info(f"Classroom sync: {classroom_counts}")
    except Exception as e:
        results["classroom_error"] = str(e)
        logger.error(f"Classroom sync failed: {e}")

    # Calendar
    try:
        calendar_count = await sync_student_calendar(student)
        results["calendar"] = calendar_count
        logger.info(f"Calendar sync: {calendar_count} events")
    except Exception as e:
        results["calendar_error"] = str(e)
        logger.error(f"Calendar sync failed: {e}")

    # Intelligence pipeline (classify + embed)
    try:
        processed = await process_student_items(student)
        results["processed"] = processed
    except Exception as e:
        results["pipeline_error"] = str(e)
        logger.error(f"Pipeline failed: {e}")

    return {"status": "done", "results": results}
