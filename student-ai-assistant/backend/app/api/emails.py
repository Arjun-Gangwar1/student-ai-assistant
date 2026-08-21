"""
Structured email API.

GET /api/emails          — paginated list with filters
GET /api/emails/{id}     — full email with attachments
GET /api/emails/search   — keyword search across subject + body
"""

from fastapi import APIRouter, HTTPException, Request, Query
from typing import Optional

from app.db.supabase import get_emails, get_email_detail, get_email_count

router = APIRouter(prefix="/api/emails", tags=["emails"])


@router.get("")
async def list_emails(
    request: Request,
    limit: int  = Query(10, ge=1, le=100),
    offset: int = Query(0, ge=0),
    date:   Optional[str] = Query(None, description="Filter by date: YYYY-MM-DD"),
    sender: Optional[str] = Query(None, description="Filter by sender name or email"),
    subject:Optional[str] = Query(None, description="Keyword in subject line"),
):
    """
    Return emails for the logged-in student.

    Examples:
      GET /api/emails?limit=10
      GET /api/emails?date=2026-06-01
      GET /api/emails?sender=ajay
      GET /api/emails?subject=assignment
    """
    student_id = request.session.get("student_id")
    if not student_id:
        raise HTTPException(status_code=401, detail="Not authenticated")

    emails = await get_emails(
        student_id=student_id,
        limit=limit,
        offset=offset,
        date=date,
        sender=sender,
        subject=subject,
    )
    total = await get_email_count(student_id)
    return {"emails": emails, "total": total, "limit": limit, "offset": offset}


@router.get("/search")
async def search_emails(
    request: Request,
    q: str = Query(..., description="Search term across subject + body"),
    limit: int = Query(10, ge=1, le=50),
):
    """Full-text search across subject and body text."""
    student_id = request.session.get("student_id")
    if not student_id:
        raise HTTPException(status_code=401, detail="Not authenticated")

    from app.db.supabase import get_supabase
    db = get_supabase()

    # Search subject (ilike) + body (ilike) — simple but works without FTS index issues
    subject_res = (
        db.table("emails")
        .select("id,subject,sender_name,sender_email,received_at,snippet,has_attachments,is_read")
        .eq("student_id", student_id)
        .ilike("subject", f"%{q}%")
        .order("received_at", desc=True)
        .limit(limit)
        .execute()
    )
    body_res = (
        db.table("emails")
        .select("id,subject,sender_name,sender_email,received_at,snippet,has_attachments,is_read")
        .eq("student_id", student_id)
        .ilike("body_text", f"%{q}%")
        .order("received_at", desc=True)
        .limit(limit)
        .execute()
    )

    # Merge + dedup
    seen, results = set(), []
    for row in (subject_res.data or []) + (body_res.data or []):
        if row["id"] not in seen:
            seen.add(row["id"])
            results.append(row)

    results.sort(key=lambda x: x["received_at"], reverse=True)
    return {"emails": results[:limit], "query": q}


@router.get("/{email_id}")
async def get_email(request: Request, email_id: str):
    """Return a single email with full body and all attachments."""
    student_id = request.session.get("student_id")
    if not student_id:
        raise HTTPException(status_code=401, detail="Not authenticated")

    email = await get_email_detail(email_id, student_id)
    if not email:
        raise HTTPException(status_code=404, detail="Email not found")
    return email
