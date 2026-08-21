"""
Google OAuth 2.0, session management, profile, and DPDP data rights.

Scope note: this app requests `gmail.readonly`, which Google classifies as
RESTRICTED. Shipping it to users beyond the 100-user unverified cap requires
OAuth verification plus an annual CASA security assessment. That obligation is
deliberate and documented in docs/PRIVACY.md — it is not something to discover
later from a Google email.
"""

import logging
import secrets

import httpx
from fastapi import APIRouter, HTTPException, Request, Response, status
from fastapi.responses import JSONResponse, RedirectResponse
from google_auth_oauthlib.flow import Flow
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from app.api.deps import CurrentStudent, CurrentStudentId
from app.config import settings
from app.db import queries

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/auth", tags=["auth"])

# Bump when the privacy notice materially changes; recorded per student so it is
# always answerable which version of the notice a given person agreed to.
CONSENT_VERSION = "2026-08-21"

BASE_SCOPES = [
    "openid",
    "https://www.googleapis.com/auth/userinfo.email",
    "https://www.googleapis.com/auth/userinfo.profile",
    # Sensitive (not restricted) — no CASA assessment required.
    "https://www.googleapis.com/auth/classroom.courses.readonly",
    "https://www.googleapis.com/auth/classroom.student-submissions.me.readonly",
    "https://www.googleapis.com/auth/classroom.announcements.readonly",
    "https://www.googleapis.com/auth/calendar.events.readonly",
]

# RESTRICTED scope — verification + CASA. Requested only when GMAIL_ENABLED.
GMAIL_SCOPE = "https://www.googleapis.com/auth/gmail.readonly"


def requested_scopes() -> list[str]:
    return BASE_SCOPES + ([GMAIL_SCOPE] if settings.gmail_enabled else [])


CLIENT_CONFIG = {
    "web": {
        "client_id": settings.google_client_id,
        "client_secret": settings.google_client_secret,
        "redirect_uris": [settings.google_redirect_uri],
        "auth_uri": "https://accounts.google.com/o/oauth2/auth",
        "token_uri": "https://oauth2.googleapis.com/token",
    }
}


def create_flow(scopes: list[str] | None = None) -> Flow:
    return Flow.from_client_config(
        CLIENT_CONFIG,
        scopes=scopes or requested_scopes(),
        redirect_uri=settings.google_redirect_uri,
    )


def _fetch_token_sync(flow: Flow, code: str) -> None:
    """google-auth's token exchange is synchronous; keep it off the event loop."""
    flow.fetch_token(code=code)


@router.get("/login")
async def login(request: Request):
    """Begin the OAuth flow."""
    flow = create_flow()
    auth_url, state = flow.authorization_url(
        access_type="offline",
        include_granted_scopes="true",
        # Forces a refresh token on repeat logins. Without it Google returns one
        # only on the very first consent, and a user who reconnects ends up with
        # an access token that expires in an hour and never renews.
        prompt="consent",
    )
    request.session["oauth_state"] = state
    return RedirectResponse(auth_url)


@router.get("/callback")
async def callback(request: Request, code: str | None = None, state: str | None = None,
                   error: str | None = None):
    """Complete the OAuth flow and establish a session."""
    if error:
        logger.info("OAuth declined by user: %s", error)
        return RedirectResponse(f"{settings.frontend_url}/?error=access_denied")

    if not code or not state:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Missing authorization code")

    # CSRF: the state must match what we issued. Previously this only warned
    # when a stored state existed, so a request with no session sailed through.
    stored_state = request.session.pop("oauth_state", None)
    if not stored_state or not secrets.compare_digest(stored_state, state):
        logger.warning("OAuth state mismatch (stored=%s, got=%s)", bool(stored_state), state[:8])
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "Invalid or expired login attempt. Please start again.",
        )

    try:
        flow = create_flow()
        await run_in_threadpool(_fetch_token_sync, flow, code)
        credentials = flow.credentials
    except Exception as exc:
        logger.error("Token exchange failed: %s", exc)
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, "Could not complete Google sign-in.")

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(
                "https://www.googleapis.com/oauth2/v2/userinfo",
                headers={"Authorization": f"Bearer {credentials.token}"},
            )
            resp.raise_for_status()
            user_info = resp.json()
    except Exception as exc:
        logger.error("Userinfo fetch failed: %s", exc)
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, "Could not read your Google profile.")

    google_id = user_info.get("id") or user_info.get("sub")
    if not google_id:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, "Google did not return a user id.")

    granted = list(credentials.scopes or [])
    student = await queries.upsert_student(
        google_id=google_id,
        email=user_info.get("email", ""),
        name=user_info.get("name"),
        scopes=granted,
        consent_version=CONSENT_VERSION,
    )
    student_id = str(student["id"])

    token_data = {
        "access_token": credentials.token,
        "refresh_token": credentials.refresh_token,
        "client_id": settings.google_client_id,
        "client_secret": settings.google_client_secret,
        "token_uri": "https://oauth2.googleapis.com/token",
        "scopes": granted,
        "expiry": credentials.expiry.isoformat() if credentials.expiry else None,
    }

    if not credentials.refresh_token:
        # Background sync cannot work without one; surface it rather than
        # letting every scheduled job fail quietly an hour from now.
        logger.warning("No refresh token returned for %s — background sync will not work", student_id)

    await queries.set_student_tokens(student_id, token_data)

    # Gmail is only on if the user actually granted the scope.
    if GMAIL_SCOPE in granted:
        await queries.set_gmail_enabled(student_id, True)

    request.session.clear()
    request.session["student_id"] = student_id

    logger.info("Login: %s (gmail_scope=%s)", user_info.get("email"), GMAIL_SCOPE in granted)
    return RedirectResponse(f"{settings.frontend_url}/dashboard")


@router.get("/me")
async def me(student: CurrentStudent):
    return {
        "student_id": str(student["id"]),
        "email": student["email"],
        "name": student["name"],
        "year": student["year"],
        "branch": student["branch"],
        "gmail_enabled": student["gmail_enabled"],
        "telegram_linked": student["telegram_chat_id"] is not None,
    }


@router.post("/logout")
async def logout(request: Request):
    request.session.clear()
    return {"status": "logged out"}


# ─── Profile ─────────────────────────────────────────────────────────────────

class ProfileUpdate(BaseModel):
    year: int | None = Field(None, ge=1, le=6)
    branch: str | None = Field(None, max_length=8)


@router.get("/profile")
async def get_profile(student: CurrentStudent):
    return {
        "id": str(student["id"]),
        "email": student["email"],
        "name": student["name"],
        "year": student["year"],
        "branch": student["branch"],
        "gmail_enabled": student["gmail_enabled"],
        "digest_time": str(student["digest_time"]),
        "telegram_linked": student["telegram_chat_id"] is not None,
    }


@router.put("/profile")
async def update_profile(body: ProfileUpdate, student_id: CurrentStudentId):
    updated = await queries.update_student_profile(student_id, body.year, body.branch)
    if updated is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Student not found")
    return {
        "id": str(updated["id"]),
        "email": updated["email"],
        "name": updated["name"],
        "year": updated["year"],
        "branch": updated["branch"],
    }


class DigestTimeUpdate(BaseModel):
    digest_time: str = Field(pattern=r"^([01]\d|2[0-3]):[0-5]\d$")


@router.put("/digest-time")
async def update_digest_time(body: DigestTimeUpdate, student_id: CurrentStudentId):
    await queries.set_digest_time(student_id, body.digest_time)
    return {"status": "updated", "digest_time": body.digest_time}


# ─── Telegram linking ────────────────────────────────────────────────────────

@router.post("/telegram/link-token")
async def create_telegram_link(student_id: CurrentStudentId):
    """
    Issue a one-shot token, returned as a deep link.

    The webhook previously read `students.telegram_link_token`, a column that
    existed in no migration, so linking could never have succeeded.
    """
    token = await queries.issue_telegram_link_token(student_id)
    bot_username = "studentai_iitdh_bot"
    return {
        "token": token,
        "deep_link": f"https://t.me/{bot_username}?start={token}",
        "instructions": "Open the link, then press Start in Telegram to connect this account.",
    }


@router.delete("/telegram/link")
async def remove_telegram_link(student_id: CurrentStudentId):
    await queries.unlink_telegram(student_id)
    return {"status": "unlinked"}


# ─── Gmail consent toggle ────────────────────────────────────────────────────

class GmailToggle(BaseModel):
    enabled: bool


@router.put("/gmail")
async def toggle_gmail(body: GmailToggle, student: CurrentStudent):
    """
    Turn Gmail ingestion on or off.

    Withdrawing consent must be as easy as giving it (DPDP Act, s.6). Turning it
    off here stops future syncs; deleting already-ingested mail is the separate
    /account endpoint below.
    """
    if body.enabled and GMAIL_SCOPE not in (student.get("google_scopes") or []):
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "Gmail access was not granted. Sign in again and approve Gmail access.",
        )
    updated = await queries.set_gmail_enabled(str(student["id"]), body.enabled)
    return {"gmail_enabled": updated["gmail_enabled"]}


# ─── DPDP Act 2023: erasure and portability ──────────────────────────────────

@router.get("/export")
async def export_my_data(student_id: CurrentStudentId):
    """Everything held about this student, as JSON (right to portability)."""
    data = await queries.export_student_data(student_id)
    return JSONResponse(
        content=jsonable(data),
        headers={"Content-Disposition": 'attachment; filename="my-student-ai-data.json"'},
    )


@router.delete("/account", status_code=status.HTTP_202_ACCEPTED)
async def delete_my_account(request: Request, student_id: CurrentStudentId, response: Response):
    """
    Erasure request.

    OAuth tokens are destroyed immediately and synchronously — access must stop
    now, not whenever a purge job next runs. Content is removed after a 7-day
    grace period so an accidental deletion is recoverable.
    """
    await queries.soft_delete_student(student_id)
    request.session.clear()
    logger.info("Account deletion requested for %s", student_id)
    return {
        "status": "deletion_scheduled",
        "tokens_revoked": True,
        "content_deleted_after_days": 7,
        "note": "Signing in again within 7 days restores the account.",
    }


def jsonable(value):
    """Recursively convert datetimes, UUIDs and Decimals for JSON output."""
    from datetime import date, datetime, time
    from decimal import Decimal
    from uuid import UUID

    if isinstance(value, dict):
        return {k: jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [jsonable(v) for v in value]
    if isinstance(value, (datetime, date, time)):
        return value.isoformat()
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, Decimal):
        return float(value)
    return value
