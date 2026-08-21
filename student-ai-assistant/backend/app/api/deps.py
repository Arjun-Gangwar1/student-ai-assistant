"""
Shared FastAPI dependencies — authentication and rate limiting.

Three endpoints previously took `student_id` straight from the URL path or the
request body and never checked it against the session:

    GET  /api/deadlines/{student_id}
    GET  /api/items/{student_id}
    POST /api/chat/ask          {"student_id": "..."}

Anyone holding a student UUID could read that student's deadlines and inbox, and
— because /chat/ask answers from retrieved context — could ask questions whose
answers quote the contents of another student's private email. `mark_read` and
`confirm_deadline` had no authentication at all.

The fix is structural rather than a check bolted onto each handler: the identity
comes from the signed session cookie only, and there is no route parameter a
caller can use to name a different student.
"""

import logging
from typing import Annotated

from fastapi import Depends, HTTPException, Request, status

from app.db import queries

logger = logging.getLogger(__name__)

SESSION_STUDENT_ID = "student_id"


async def get_current_student_id(request: Request) -> str:
    """The authenticated student's id, from the signed session cookie."""
    student_id = request.session.get(SESSION_STUDENT_ID)
    if not student_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated. Sign in with Google first.",
            headers={"WWW-Authenticate": "Session"},
        )
    return student_id


async def get_current_student(
    request: Request,
    student_id: Annotated[str, Depends(get_current_student_id)],
) -> dict:
    """
    The full student record.

    Re-read from the database on every request rather than trusted from the
    cookie, so that a deleted account stops working immediately instead of
    whenever its session happens to expire.
    """
    student = await queries.get_student(student_id)
    if student is None:
        # Account deleted, or the session key rotated. Drop the stale cookie so
        # the browser stops replaying it on every subsequent request.
        request.session.clear()
        logger.info("Cleared stale session for student_id=%s", student_id)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Session is no longer valid. Please sign in again.",
        )
    return student


async def get_current_student_with_tokens(
    student_id: Annotated[str, Depends(get_current_student_id)],
) -> dict:
    """
    Student record including decrypted Google OAuth tokens.

    Deliberately a separate dependency: most endpoints have no business touching
    OAuth tokens, and decrypting them by default would put them into scopes that
    never need them.
    """
    student = await queries.get_student_with_tokens(student_id)
    if student is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Session is no longer valid. Please sign in again.",
        )
    return student


CurrentStudentId = Annotated[str, Depends(get_current_student_id)]
CurrentStudent = Annotated[dict, Depends(get_current_student)]
CurrentStudentWithTokens = Annotated[dict, Depends(get_current_student_with_tokens)]
