"""
Deadlines API — the radar, plus the confirm/correct loop.

Every route is scoped to the session's student. The old
`GET /api/deadlines/{student_id}` shape is gone entirely: a path parameter
naming the subject of the request is an invitation to pass someone else's id.
"""

import logging
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel, Field

from app.api.deps import CurrentStudentId
from app.db import queries
from app.utils.date_utils import days_until, hours_until, priority_from_deadline

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/deadlines", tags=["deadlines"])


class DeadlineOut(BaseModel):
    id: str
    title: str
    due_at: datetime
    source: str
    confirmed: bool
    confidence: float
    days_left: int
    hours_left: int
    priority_label: str
    needs_review: bool


@router.get("")
async def list_deadlines(
    student_id: CurrentStudentId,
    days: int = Query(14, ge=1, le=365),
):
    """Upcoming deadlines for the signed-in student."""
    raw = await queries.get_upcoming_deadlines(student_id, days=days)

    deadlines = []
    for d in raw:
        due_at: datetime = d["due_at"]
        deadlines.append(
            DeadlineOut(
                id=str(d["id"]),
                title=d["title"],
                due_at=due_at,
                source=d["source"],
                confirmed=d["confirmed"],
                confidence=d["confidence"],
                days_left=days_until(due_at),
                hours_left=hours_until(due_at),
                priority_label=priority_from_deadline(due_at),
                # Trust rule: anything the LLM extracted below the confidence bar
                # must be surfaced for review rather than presented as fact.
                needs_review=not d["confirmed"],
            ).model_dump()
        )

    return {"deadlines": deadlines, "total": len(deadlines)}


class ConfirmBody(BaseModel):
    confirmed: bool
    corrected_due_at: Optional[datetime] = None


@router.patch("/{deadline_id}/confirm")
async def confirm_deadline(
    deadline_id: str,
    body: ConfirmBody,
    student_id: CurrentStudentId,
):
    """
    Confirm, correct, or dismiss an extracted deadline.

    Every correction is also recorded as feedback — this is the dataset that
    makes per-college extraction better than a generic model, and it is the only
    part of the product a large vendor is unlikely to replicate.
    """
    updated = await queries.confirm_deadline(
        deadline_id=deadline_id,
        student_id=student_id,
        confirmed=body.confirmed,
        corrected_due_at=body.corrected_due_at,
    )
    if updated is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Deadline not found")

    await queries.save_feedback(
        student_id=student_id,
        deadline_id=deadline_id,
        item_id=str(updated["item_id"]) if updated.get("item_id") else None,
        was_correct=body.confirmed,
        corrected_deadline=body.corrected_due_at,
        model_output={
            "extracted_due_at": updated["due_at"].isoformat(),
            "confidence": updated["confidence"],
            "source": updated["source"],
        },
    )

    return {"status": "updated", "deadline_id": deadline_id, "confirmed": body.confirmed}


class FeedbackBody(BaseModel):
    item_id: Optional[str] = None
    deadline_id: Optional[str] = None
    was_correct: bool
    corrected_deadline: Optional[datetime] = None
    corrected_category: Optional[str] = Field(None, max_length=32)
    notes: Optional[str] = Field(None, max_length=2000)


@router.post("/feedback", status_code=status.HTTP_201_CREATED)
async def submit_feedback(body: FeedbackBody, student_id: CurrentStudentId):
    """Free-form correction feedback on any extraction."""
    if body.item_id:
        # Confirm the item belongs to this student before attaching feedback,
        # so feedback cannot be written against another student's row.
        if await queries.get_item(body.item_id, student_id) is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Item not found")

    saved = await queries.save_feedback(
        student_id=student_id,
        item_id=body.item_id,
        deadline_id=body.deadline_id,
        was_correct=body.was_correct,
        corrected_deadline=body.corrected_deadline,
        corrected_category=body.corrected_category,
        notes=body.notes,
    )
    return {"status": "saved", "feedback_id": str(saved["id"])}
