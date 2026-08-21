"""
Telegram webhook endpoint.
Receives messages from users and routes commands to the RAG engine.
"""

import logging
from fastapi import APIRouter, Request, HTTPException, Header
from app.config import settings
from app.db.supabase import get_supabase
from app.rag.generator import answer_question
from app.alerts.telegram_bot import send_message
from app.connectors.classroom import sync_student_classroom
from app.connectors.calendar_conn import sync_student_calendar
from app.intelligence.pipeline import process_student_items

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/telegram", tags=["telegram"])

HELP_TEXT = """*Student AI Assistant* 🎓

Commands:
/ask <question> — Ask anything about your deadlines, assignments, or notices
/deadlines — Show upcoming deadlines
/sync — Manually sync your Classroom and Calendar
/help — Show this message

Examples:
`/ask what assignments are due this week?`
`/ask is there any exam in the next 7 days?`
`/ask what's the mess menu today?`"""


async def _get_student_by_telegram_id(chat_id: int) -> dict | None:
    db = get_supabase()
    res = (
        db.table("students")
        .select("*")
        .eq("telegram_chat_id", chat_id)
        .maybe_single()
        .execute()
    )
    return res.data


@router.post("/webhook")
async def telegram_webhook(
    request: Request,
    x_telegram_bot_api_secret_token: str | None = Header(None),
):
    # Verify webhook secret
    if (
        settings.telegram_webhook_secret
        and x_telegram_bot_api_secret_token != settings.telegram_webhook_secret
    ):
        raise HTTPException(status_code=403, detail="Invalid secret")

    body = await request.json()
    message = body.get("message", {})
    if not message:
        return {"ok": True}

    chat_id = message.get("chat", {}).get("id")
    text = message.get("text", "").strip()

    if not chat_id or not text:
        return {"ok": True}

    student = await _get_student_by_telegram_id(chat_id)

    # ── /start — link Telegram account ────────────────────────────────────
    if text.startswith("/start"):
        token = text.replace("/start", "").strip()
        if token and not student:
            # link_token flow: connect Telegram chat_id to existing student
            db = get_supabase()
            res = db.table("students").select("*").eq("telegram_link_token", token).maybe_single().execute()
            if res.data:
                db.table("students").update({"telegram_chat_id": chat_id}).eq("id", res.data["id"]).execute()
                await send_message(chat_id, f"✅ Account linked! Welcome {res.data.get('name', '')}!\n\n" + HELP_TEXT)
                return {"ok": True}
        await send_message(chat_id, "Welcome! Login at the web app first, then link your Telegram.\n\n" + HELP_TEXT)
        return {"ok": True}

    if not student:
        await send_message(chat_id, "Please login via the web app first to connect your account.")
        return {"ok": True}

    # ── /help ──────────────────────────────────────────────────────────────
    if text == "/help":
        await send_message(chat_id, HELP_TEXT)

    # ── /deadlines ─────────────────────────────────────────────────────────
    elif text == "/deadlines":
        from app.alerts.digest import build_digest
        digest = await build_digest(student)
        await send_message(chat_id, digest)

    # ── /sync ──────────────────────────────────────────────────────────────
    elif text == "/sync":
        await send_message(chat_id, "🔄 Syncing your data... (this takes ~30 seconds)")
        await sync_student_classroom(student)
        await sync_student_calendar(student)
        await process_student_items(student)
        await send_message(chat_id, "✅ Sync complete! Your data is up to date.")

    # ── /ask <question> ────────────────────────────────────────────────────
    elif text.startswith("/ask ") or (not text.startswith("/") and len(text) > 5):
        question = text.replace("/ask", "").strip() if text.startswith("/ask") else text
        await send_message(chat_id, "🤔 Thinking...")
        result = await answer_question(question=question, student_id=student["id"])
        answer = result["answer"]
        await send_message(chat_id, answer)

    else:
        await send_message(chat_id, "Type /help to see available commands.")

    return {"ok": True}
