"""
Items API — fetch inbox items with filtering.
"""

from fastapi import APIRouter, Query
from app.db.supabase import get_supabase

router = APIRouter(prefix="/api/items", tags=["items"])


@router.get("/{student_id}")
async def list_items(
    student_id: str,
    priority: str | None = Query(None),
    category: str | None = Query(None),
    unread_only: bool = Query(False),
    limit: int = Query(20, le=100),
):
    db = get_supabase()
    query = (
        db.table("items")
        .select("id, title, summary, source, category, priority, relevance_score, deadline, is_read, created_at")
        .eq("student_id", student_id)
        .order("created_at", desc=True)
        .limit(limit)
    )

    if priority:
        query = query.eq("priority", priority.upper())
    if category:
        query = query.eq("category", category.lower())
    if unread_only:
        query = query.eq("is_read", False)

    res = query.execute()
    return {"items": res.data or [], "total": len(res.data or [])}


@router.patch("/{item_id}/read")
async def mark_read(item_id: str):
    db = get_supabase()
    db.table("items").update({"is_read": True}).eq("id", item_id).execute()
    return {"status": "marked as read"}
