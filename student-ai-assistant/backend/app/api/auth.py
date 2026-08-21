"""
Google OAuth 2.0 flow for sensitive scopes (Classroom + Calendar).
Tokens stored in Supabase. No Gmail scope here.
"""

import logging
import httpx
from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import RedirectResponse
from google_auth_oauthlib.flow import Flow
from starlette.concurrency import run_in_threadpool

from app.config import settings
from pydantic import BaseModel
from app.db.supabase import upsert_student, update_student_tokens, get_student_profile, update_student_profile

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/auth", tags=["auth"])

SCOPES = [
    "openid",
    "https://www.googleapis.com/auth/userinfo.email",
    "https://www.googleapis.com/auth/userinfo.profile",
    "https://www.googleapis.com/auth/classroom.courses.readonly",
    "https://www.googleapis.com/auth/classroom.student-submissions.me.readonly",
    "https://www.googleapis.com/auth/classroom.announcements.readonly",
    "https://www.googleapis.com/auth/calendar.events.readonly",
    "https://www.googleapis.com/auth/gmail.readonly",
]

CLIENT_CONFIG = {
    "web": {
        "client_id": settings.google_client_id,
        "client_secret": settings.google_client_secret,
        "redirect_uris": [settings.google_redirect_uri],
        "auth_uri": "https://accounts.google.com/o/oauth2/auth",
        "token_uri": "https://oauth2.googleapis.com/token",
    }
}


def create_flow() -> Flow:
    return Flow.from_client_config(
        CLIENT_CONFIG,
        scopes=SCOPES,
        redirect_uri=settings.google_redirect_uri,
    )


def _fetch_token_sync(flow: Flow, code: str) -> None:
    """Run synchronous token fetch in thread pool."""
    import os
    os.environ["OAUTHLIB_INSECURE_TRANSPORT"] = "1"  # allow http for localhost
    flow.fetch_token(code=code)


@router.get("/login")
async def login(request: Request):
    """Redirect user to Google OAuth consent screen."""
    flow = create_flow()
    auth_url, state = flow.authorization_url(
        access_type="offline",
        prompt="consent",
    )
    request.session["oauth_state"] = state
    return RedirectResponse(auth_url)


@router.get("/callback")
async def callback(request: Request, code: str, state: str):
    """Handle OAuth callback, fetch user info, upsert student."""

    # State check — warn but don't block in development
    stored_state = request.session.get("oauth_state")
    if stored_state and stored_state != state:
        logger.warning(f"State mismatch: stored={stored_state}, got={state}")
        raise HTTPException(status_code=400, detail="Invalid OAuth state. Please try logging in again.")

    try:
        # Fetch token in thread pool (sync operation)
        flow = create_flow()
        await run_in_threadpool(_fetch_token_sync, flow, code)
        credentials = flow.credentials
    except Exception as e:
        logger.error(f"Token fetch failed: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to get token from Google: {str(e)}")

    # Get user info via direct HTTP call (no discovery doc needed)
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                "https://www.googleapis.com/oauth2/v2/userinfo",
                headers={"Authorization": f"Bearer {credentials.token}"},
            )
            resp.raise_for_status()
            user_info = resp.json()
    except Exception as e:
        logger.error(f"Userinfo fetch failed: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to get user info from Google: {str(e)}")

    google_id = user_info.get("id") or user_info.get("sub")
    email = user_info.get("email", "")
    name = user_info.get("name", "")

    if not google_id:
        raise HTTPException(status_code=500, detail="Could not get user ID from Google")

    # Upsert student in Supabase
    student = await upsert_student({
        "google_id": google_id,
        "email": email,
        "name": name,
    })

    # Store tokens for later API calls (Classroom, Calendar)
    token_data = {
        "access_token": credentials.token,
        "refresh_token": credentials.refresh_token,
        "client_id": settings.google_client_id,
        "client_secret": settings.google_client_secret,
        "token_uri": "https://oauth2.googleapis.com/token",
        "scopes": SCOPES,
    }
    await update_student_tokens(student["id"], token_data)

    # Save to session
    request.session["student_id"] = student["id"]
    request.session["student_email"] = email
    request.session["student_name"] = name

    logger.info(f"Login successful: {email}")
    return RedirectResponse(f"{settings.frontend_url}/dashboard")


@router.get("/me")
async def me(request: Request):
    student_id = request.session.get("student_id")
    if not student_id:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return {
        "student_id": student_id,
        "email": request.session.get("student_email"),
        "name": request.session.get("student_name"),
    }


@router.post("/logout")
async def logout(request: Request):
    request.session.clear()
    return {"status": "logged out"}


class ProfileUpdate(BaseModel):
    year:   int | None = None
    branch: str | None = None


@router.get("/profile")
async def get_profile(request: Request):
    student_id = request.session.get("student_id")
    if not student_id:
        raise HTTPException(status_code=401, detail="Not authenticated")
    profile = await get_student_profile(student_id)
    if not profile:
        raise HTTPException(status_code=404, detail="Student not found")
    return profile


@router.put("/profile")
async def update_profile(request: Request, body: ProfileUpdate):
    student_id = request.session.get("student_id")
    if not student_id:
        raise HTTPException(status_code=401, detail="Not authenticated")
    if body.year is not None and not (1 <= body.year <= 6):
        raise HTTPException(status_code=422, detail="year must be between 1 and 6")
    updated = await update_student_profile(student_id, body.year, body.branch)
    return updated
