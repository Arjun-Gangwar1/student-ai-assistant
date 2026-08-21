"""
Shared Google API credential handling.

Each connector previously built its own `Credentials` with its own hardcoded
scope list, and none of them persisted a refreshed access token. google-auth
refreshes in memory, so every scheduled run spent an extra round trip to Google
re-refreshing a token it had already refreshed minutes earlier.
"""

import logging
from datetime import datetime, timezone

from google.auth.transport.requests import Request as GoogleRequest
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from starlette.concurrency import run_in_threadpool

from app.db import queries

logger = logging.getLogger(__name__)


class GoogleAuthError(RuntimeError):
    """Credentials are missing, expired beyond recovery, or revoked."""


def build_credentials(token_data: dict) -> Credentials:
    """
    Construct Credentials from a stored token blob.

    Scopes come from what the student actually granted rather than from a
    per-connector constant — asking for a scope the user did not grant makes
    google-auth refuse the refresh outright.
    """
    if not token_data:
        raise GoogleAuthError("no stored tokens")

    expiry = None
    if raw_expiry := token_data.get("expiry"):
        try:
            parsed = datetime.fromisoformat(raw_expiry)
            # google-auth compares expiry against a naive UTC now(); handing it
            # an aware datetime raises "can't subtract offset-naive and
            # offset-aware datetimes" deep inside the refresh path.
            expiry = parsed.astimezone(timezone.utc).replace(tzinfo=None) if parsed.tzinfo else parsed
        except (ValueError, TypeError):
            expiry = None

    return Credentials(
        token=token_data.get("access_token"),
        refresh_token=token_data.get("refresh_token"),
        token_uri=token_data.get("token_uri", "https://oauth2.googleapis.com/token"),
        client_id=token_data.get("client_id"),
        client_secret=token_data.get("client_secret"),
        scopes=token_data.get("scopes") or None,
        expiry=expiry,
    )


async def get_credentials(student: dict) -> Credentials:
    """
    Valid credentials for a student, refreshing and persisting if needed.
    """
    token_data = student.get("google_tokens")
    if not token_data:
        raise GoogleAuthError(f"student {student['id']} has no Google tokens")

    credentials = build_credentials(token_data)

    if credentials.valid:
        return credentials

    if not credentials.refresh_token:
        raise GoogleAuthError(
            f"student {student['id']} has an expired token and no refresh token — "
            "they must sign in again"
        )

    try:
        await run_in_threadpool(credentials.refresh, GoogleRequest())
    except Exception as exc:
        # A revoked grant (user removed access in their Google account) looks
        # like any other refresh failure, so say what the caller should do.
        raise GoogleAuthError(
            f"token refresh failed for student {student['id']}: {exc}. "
            "The grant may have been revoked; they must reconnect."
        ) from exc

    # Persist so the next run starts from a valid token.
    await queries.update_access_token(
        student_id=str(student["id"]),
        access_token=credentials.token,
        expiry=credentials.expiry.isoformat() if credentials.expiry else None,
    )
    student["google_tokens"] = {
        **token_data,
        "access_token": credentials.token,
        "expiry": credentials.expiry.isoformat() if credentials.expiry else None,
    }
    logger.debug("Refreshed Google access token for %s", student["id"])
    return credentials


async def build_service(student: dict, api: str, version: str):
    """
    Build a Google API client.

    `build` performs blocking discovery I/O, so it runs in a threadpool;
    cache_discovery is off because the default file cache warns loudly and is
    useless in a container.
    """
    credentials = await get_credentials(student)
    return await run_in_threadpool(
        lambda: build(api, version, credentials=credentials, cache_discovery=False)
    )


def has_scope(student: dict, needle: str) -> bool:
    return any(needle in scope for scope in (student.get("google_scopes") or []))
