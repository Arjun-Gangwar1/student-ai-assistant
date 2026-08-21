"""
Structured email API.

    GET /api/emails            paginated list with filters
    GET /api/emails/search     full-text over subject + sender + body
    GET /api/emails/{id}       one email with attachments

Route order matters: /search is declared before /{email_id}, otherwise "search"
is captured as an id.
"""

import logging
from typing import Optional

from fastapi import APIRouter, HTTPException, Query, status

from app.api.deps import CurrentStudentId
from app.db import queries

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/emails", tags=["emails"])


def _serialise(rows: list[dict]) -> list[dict]:
    for row in rows:
        row["id"] = str(row["id"])
        if row.get("item_id"):
            row["item_id"] = str(row["item_id"])
    return rows


@router.get("")
async def list_emails(
    student_id: CurrentStudentId,
    limit: int = Query(10, ge=1, le=100),
    offset: int = Query(0, ge=0),
    date: Optional[str] = Query(None, description="Calendar date in IST: YYYY-MM-DD"),
    sender: Optional[str] = Query(None, description="Sender name or address, partial match"),
    subject: Optional[str] = Query(None, description="Keyword in the subject line"),
):
    emails = await queries.list_emails(
        student_id=student_id,
        limit=limit,
        offset=offset,
        date=date,
        sender=sender,
        subject=subject,
    )
    return {
        "emails": _serialise(emails),
        "total": await queries.count_emails(student_id),
        "limit": limit,
        "offset": offset,
    }


@router.get("/search")
async def search_emails(
    student_id: CurrentStudentId,
    q: str = Query(..., min_length=1, max_length=200),
    limit: int = Query(10, ge=1, le=50),
):
    """
    Full-text search.

    Runs against the generated `search_vector` column with a single ranked
    query, rather than the previous two ILIKE scans merged in Python — which
    could not rank, and could not use an index.
    """
    return {"emails": _serialise(await queries.search_emails(student_id, q, limit)), "query": q}


@router.get("/{email_id}")
async def get_email(email_id: str, student_id: CurrentStudentId):
    email = await queries.get_email_detail(email_id, student_id)
    if email is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Email not found")

    email["id"] = str(email["id"])
    email["student_id"] = str(email["student_id"])
    if email.get("item_id"):
        email["item_id"] = str(email["item_id"])
    for attachment in email.get("attachments", []):
        attachment["id"] = str(attachment["id"])
    return email
