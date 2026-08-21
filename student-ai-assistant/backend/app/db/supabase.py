from supabase import create_client, Client
from functools import lru_cache
from app.config import settings


@lru_cache
def get_supabase() -> Client:
    return create_client(settings.supabase_url, settings.supabase_service_role_key)


# ─── Student helpers ──────────────────────────────────────────────────────────

async def get_student_by_google_id(google_id: str) -> dict | None:
    db = get_supabase()
    res = db.table("students").select("*").eq("google_id", google_id).maybe_single().execute()
    return res.data


async def upsert_student(data: dict) -> dict:
    db = get_supabase()
    res = (
        db.table("students")
        .upsert(data, on_conflict="google_id")
        .execute()
    )
    return res.data[0]


async def get_all_students() -> list[dict]:
    db = get_supabase()
    res = db.table("students").select("*").execute()
    return res.data or []


async def update_student_tokens(student_id: str, tokens: dict) -> None:
    db = get_supabase()
    db.table("students").update({"google_tokens": tokens}).eq("id", student_id).execute()


async def get_student_profile(student_id: str) -> dict | None:
    db = get_supabase()
    res = (
        db.table("students")
        .select("id, email, name, year, branch")
        .eq("id", student_id)
        .maybe_single()
        .execute()
    )
    return res.data


async def update_student_profile(student_id: str, year: int | None, branch: str | None) -> dict:
    db = get_supabase()
    # Always include both fields so NULL values can clear existing data
    updates: dict = {"year": year, "branch": branch}
    db.table("students").update(updates).eq("id", student_id).execute()
    # Re-fetch to return current state (Supabase may return empty data on NULL updates)
    res = db.table("students").select("id,email,name,year,branch").eq("id", student_id).maybe_single().execute()
    return res.data


# ─── Item helpers ─────────────────────────────────────────────────────────────

async def upsert_item(data: dict) -> dict:
    """Insert or update an item; dedup by (student_id, source, source_id)."""
    db = get_supabase()
    res = (
        db.table("items")
        .upsert(data, on_conflict="student_id,source,source_id")
        .execute()
    )
    return res.data[0]


async def get_unprocessed_items(student_id: str, limit: int = 50) -> list[dict]:
    """Items that have been stored but not yet classified/embedded."""
    db = get_supabase()
    res = (
        db.table("items")
        .select("*")
        .eq("student_id", student_id)
        .is_("embedding", "null")
        .limit(limit)
        .execute()
    )
    return res.data or []


async def update_item(item_id: str, data: dict) -> dict:
    db = get_supabase()
    res = db.table("items").update(data).eq("id", item_id).execute()
    return res.data[0]


# ─── Deadline helpers ─────────────────────────────────────────────────────────

async def upsert_deadline(data: dict) -> dict:
    db = get_supabase()
    res = db.table("deadlines").upsert(data).execute()
    return res.data[0]


async def get_upcoming_deadlines(student_id: str, days: int = 7) -> list[dict]:
    from datetime import datetime, timedelta, timezone
    now = datetime.now(timezone.utc)
    cutoff = now + timedelta(days=days)
    db = get_supabase()
    res = (
        db.table("deadlines")
        .select("*")
        .eq("student_id", student_id)
        .gte("due_at", now.isoformat())
        .lte("due_at", cutoff.isoformat())
        .order("due_at")
        .execute()
    )
    return res.data or []


async def get_deadlines_needing_alert(alert_field: str) -> list[dict]:
    """Return deadlines where a specific alert has not been sent yet and is due."""
    from datetime import datetime, timedelta, timezone

    windows = {
        "alert_sent_48h": timedelta(hours=48),
        "alert_sent_24h": timedelta(hours=24),
        "alert_sent_6h":  timedelta(hours=6),
    }
    window = windows[alert_field]
    now = datetime.now(timezone.utc)
    cutoff = now + window + timedelta(minutes=30)  # 30-min lookahead buffer

    db = get_supabase()
    res = (
        db.table("deadlines")
        .select("*, students(telegram_chat_id, name)")
        .eq(alert_field, False)
        .lte("due_at", cutoff.isoformat())
        .gte("due_at", now.isoformat())
        .execute()
    )
    return res.data or []


async def mark_alert_sent(deadline_id: str, alert_field: str) -> None:
    db = get_supabase()
    db.table("deadlines").update({alert_field: True}).eq("id", deadline_id).execute()


# ─── Feedback helpers ─────────────────────────────────────────────────────────

async def save_feedback(data: dict) -> dict:
    db = get_supabase()
    res = db.table("extraction_feedback").insert(data).execute()
    return res.data[0]


# ─── Alert history ────────────────────────────────────────────────────────────

async def log_alert(data: dict) -> None:
    db = get_supabase()
    db.table("alerts").insert(data).execute()


# ─── Email helpers ────────────────────────────────────────────────────────────

async def upsert_email(data: dict) -> dict:
    """Insert or update an email row; dedup by (student_id, message_id)."""
    db = get_supabase()
    res = (
        db.table("emails")
        .upsert(data, on_conflict="student_id,message_id")
        .execute()
    )
    return res.data[0]


async def upsert_email_attachment(data: dict) -> dict:
    """Insert or update an email attachment; dedup by (email_id, filename)."""
    db = get_supabase()
    res = (
        db.table("email_attachments")
        .upsert(data, on_conflict="email_id,filename")
        .execute()
    )
    return res.data[0]


async def get_emails(
    student_id: str,
    limit: int = 10,
    offset: int = 0,
    date: str | None = None,          # "YYYY-MM-DD"
    sender: str | None = None,        # name or email (partial match)
    subject: str | None = None,       # keyword in subject
    query: str | None = None,         # full-text across subject+body
) -> list[dict]:
    """
    Filtered email listing for a student.
    Returns emails ordered by received_at DESC.
    """
    db = get_supabase()
    q = (
        db.table("emails")
        .select("id,subject,sender_name,sender_email,received_at,snippet,"
                "labels,has_attachments,attachment_count,is_read,item_id")
        .eq("student_id", student_id)
    )

    if date:
        # Filter by calendar date (UTC)
        q = q.gte("received_at", f"{date}T00:00:00+00:00")
        q = q.lt("received_at",  f"{date}T23:59:59+00:00")

    if sender:
        # ilike searches sender_name OR sender_email
        q = q.or_(f"sender_name.ilike.%{sender}%,sender_email.ilike.%{sender}%")

    if subject:
        q = q.ilike("subject", f"%{subject}%")

    res = (
        q.order("received_at", desc=True)
        .range(offset, offset + limit - 1)
        .execute()
    )
    return res.data or []


async def get_email_detail(email_id: str, student_id: str) -> dict | None:
    """Return a single email with its attachments."""
    db = get_supabase()
    res = (
        db.table("emails")
        .select("*")
        .eq("id", email_id)
        .eq("student_id", student_id)
        .maybe_single()
        .execute()
    )
    if not res.data:
        return None
    email = res.data
    atts = (
        db.table("email_attachments")
        .select("id,filename,mime_type,size_bytes,attachment_type,url,extracted_text")
        .eq("email_id", email_id)
        .execute()
    )
    email["attachments"] = atts.data or []
    return email


async def get_email_count(student_id: str) -> int:
    db = get_supabase()
    res = db.table("emails").select("id", count="exact").eq("student_id", student_id).execute()
    return res.count or 0
