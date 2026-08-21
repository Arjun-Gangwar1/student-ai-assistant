"""
Deadlines API — list upcoming, confirm/correct extracted deadlines.
"""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional
from datetime import datetime

from app.db.supabase import get_upcoming_deadlines, get_supabase, save_feedback
from app.utils.date_utils import days_until, hours_until, priority_from_deadline

router = APIRouter(prefix="/api/deadlines", tags=["deadlines"])


@router.get("/{student_id}")
async def list_deadlines(student_id: str, days: int = 14):
    raw = await get_upcoming_deadlines(student_id, days=days)

    enriched = []
    for d in raw:
        due_str = d.get("due_at", "")
        try:
            due_dt = datetime.fromisoformat(due_str)
            d["days_left"] = days_until(due_dt)
            d["hours_left"] = hours_until(due_dt)
            d["priority_label"] = priority_from_deadline(due_dt)
        except Exception:
            pass
        enriched.append(d)

    return {"deadlines": enriched, "total": len(enriched)}


class ConfirmBody(BaseModel):
    confirmed: bool
    corrected_due_at: Optional[str] = None


@router.patch("/{deadline_id}/confirm")
async def confirm_deadline(deadline_id: str, body: ConfirmBody):
    db = get_supabase()

    update: dict = {"confirmed": body.confirmed}
    if body.corrected_due_at:
        update["due_at"] = body.corrected_due_at

    res = db.table("deadlines").update(update).eq("id", deadline_id).execute()
    if not res.data:
        raise HTTPException(status_code=404, detail="Deadline not found")

    return {"status": "updated", "deadline": res.data[0]}


class FeedbackBody(BaseModel):
    item_id: str
    student_id: str
    was_correct: bool
    corrected_deadline: Optional[str] = None
    corrected_category: Optional[str] = None
    notes: Optional[str] = None


@router.post("/feedback")
async def submit_feedback(body: FeedbackBody):
    """Submit correction feedback — feeds the moat-building dataset."""
    await save_feedback(body.model_dump())
    return {"status": "feedback saved, thank you!"}
