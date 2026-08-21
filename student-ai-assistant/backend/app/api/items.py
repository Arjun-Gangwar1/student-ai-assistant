"""
Items API — the unified inbox across Classroom, Calendar, Gmail and the college
website. Session-scoped throughout.
"""

import logging

from fastapi import APIRouter, HTTPException, Query, status

from app.api.deps import CurrentStudentId
from app.db import queries

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/items", tags=["items"])

VALID_PRIORITIES = {"HIGH", "MEDIUM", "LOW"}
VALID_CATEGORIES = {
    "academic", "admin", "event", "transport",
    "mess", "placement", "hostel", "general",
}
VALID_SOURCES = {"classroom", "calendar", "gmail", "gmail_attachment", "website", "telegram"}


@router.get("")
async def list_items(
    student_id: CurrentStudentId,
    priority: str | None = Query(None),
    category: str | None = Query(None),
    source: str | None = Query(None),
    unread_only: bool = Query(False),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
):
    """Filtered inbox for the signed-in student."""
    # Reject unknown filter values rather than silently returning everything —
    # a typo'd filter that quietly widens the result set is worse than an error.
    if priority and priority.upper() not in VALID_PRIORITIES:
        raise HTTPException(422, f"priority must be one of {sorted(VALID_PRIORITIES)}")
    if category and category.lower() not in VALID_CATEGORIES:
        raise HTTPException(422, f"category must be one of {sorted(VALID_CATEGORIES)}")
    if source and source.lower() not in VALID_SOURCES:
        raise HTTPException(422, f"source must be one of {sorted(VALID_SOURCES)}")

    items = await queries.list_items(
        student_id=student_id,
        priority=priority,
        category=category,
        source=source,
        unread_only=unread_only,
        limit=limit,
        offset=offset,
    )
    for item in items:
        item["id"] = str(item["id"])

    return {"items": items, "count": len(items), "limit": limit, "offset": offset}


@router.get("/{item_id}")
async def get_item(item_id: str, student_id: CurrentStudentId):
    """One item in full. Scoped by student, so a foreign id returns 404."""
    item = await queries.get_item(item_id, student_id)
    if item is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Item not found")
    item.pop("embedding", None)      # 768 floats are of no use to a client
    item.pop("search_vector", None)
    item["id"] = str(item["id"])
    item["student_id"] = str(item["student_id"])
    return item


@router.patch("/{item_id}/read")
async def mark_read(item_id: str, student_id: CurrentStudentId):
    """Mark as read. Previously unauthenticated — any caller could flip any row."""
    if not await queries.mark_item_read(item_id, student_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Item not found")
    return {"status": "read", "item_id": item_id}
