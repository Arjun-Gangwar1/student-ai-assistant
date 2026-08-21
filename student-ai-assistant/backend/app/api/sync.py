"""
Manual sync trigger.

Rate-limited: a sync is a burst of Google API calls plus a run of the LLM
pipeline, so a client that retries in a loop is expensive in both quota and
money.
"""

import logging

from fastapi import APIRouter, HTTPException, status

from app.api.deps import CurrentStudentWithTokens
from app.config import settings
from app.utils.ratelimit import RateLimiter
from app.workers.sync_worker import sync_one_student

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/sync", tags=["sync"])

_sync_limiter = RateLimiter(
    max_calls=settings.sync_rate_limit_per_hour,
    window_seconds=3600,
    name="sync",
)


@router.post("/now")
async def sync_now(student: CurrentStudentWithTokens):
    """Pull Classroom, Calendar and (if enabled) Gmail, then run the pipeline."""
    student_id = str(student["id"])

    allowed, remaining, retry_after = _sync_limiter.check(student_id)
    if not allowed:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"Sync limit reached. Try again in {retry_after // 60 + 1} minutes.",
            headers={"Retry-After": str(retry_after)},
        )

    if not student.get("google_tokens"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Google account is not connected. Sign in again to reconnect.",
        )

    results = await sync_one_student(student)
    return {"status": "done", "results": results, "syncs_remaining_this_hour": remaining}


@router.get("/status")
async def sync_status(student: CurrentStudentWithTokens):
    """What is connected and what is still waiting to be processed."""
    from app.db import queries

    student_id = str(student["id"])
    scopes = student.get("google_scopes") or []

    return {
        "google_connected": bool(student.get("google_tokens")),
        "has_refresh_token": bool((student.get("google_tokens") or {}).get("refresh_token")),
        "connected_sources": {
            "classroom": any("classroom" in s for s in scopes),
            "calendar": any("calendar" in s for s in scopes),
            "gmail": student.get("gmail_enabled", False) and any("gmail" in s for s in scopes),
        },
        "unprocessed_items": await queries.count_unprocessed_items(student_id),
        "email_count": await queries.count_emails(student_id),
        "syncs_remaining_this_hour": _sync_limiter.peek(student_id),
    }
