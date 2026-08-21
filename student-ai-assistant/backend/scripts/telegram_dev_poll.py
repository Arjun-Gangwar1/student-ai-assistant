"""
Dev-only Telegram polling bot.
Run this in a separate terminal while developing locally:
    python scripts/telegram_dev_poll.py
Handles /start /help /deadlines /sync /ask commands.
"""
import asyncio
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.config import settings
from app.alerts.telegram_bot import send_message
from app.db.supabase import get_supabase

HELP_TEXT = """*Student AI Assistant* 🎓

Commands:
/ask <question> — Ask about deadlines, assignments, notices
/deadlines — Show upcoming deadlines
/emails [N] — Show last N emails (default 5)
/emails from <name> — Emails from a sender
/emails date <YYYY-MM-DD> — Emails on a date
/emails subject <keyword> — Search by subject
/sync — Sync Classroom + Calendar + Gmail
/help — Show this message

Example: `/ask what quizzes do I have?`
Example: `/emails from professor`"""


async def get_students(chat_id: int) -> list[dict]:
    """Return all students linked to this Telegram chat_id (may span multiple accounts)."""
    db = get_supabase()
    res = db.table("students").select("*").eq("telegram_chat_id", chat_id).execute()
    return res.data or []


async def get_primary_student(chat_id: int) -> dict | None:
    """Return the first linked student (used for sync/deadlines which need Classroom/Calendar tokens)."""
    students = await get_students(chat_id)
    return students[0] if students else None


def _format_email_for_telegram(email: dict, index: int) -> str:
    """Format a single email into a readable Telegram message block."""
    from datetime import datetime, timezone
    received = email.get("received_at", "")
    try:
        dt = datetime.fromisoformat(received).astimezone(timezone.utc)
        date_str = dt.strftime("%d %b %Y, %I:%M %p UTC")
    except Exception:
        date_str = received[:16]

    sender = email.get("sender_name") or email.get("sender_email") or "Unknown"
    subject = email.get("subject", "(no subject)")
    import html as _html, re as _re
    raw_snippet = email.get("snippet", "")
    raw_snippet = _html.unescape(raw_snippet)
    # Remove zero-width / invisible chars
    raw_snippet = _re.sub(r"[­͏​-‏‪-‮⁠﻿]", "", raw_snippet)
    # Convert unicode spaces (nbsp, figure space, etc.) → regular space
    raw_snippet = _re.sub(r"[   -    　]", " ", raw_snippet)
    raw_snippet = _re.sub(r" {2,}", " ", raw_snippet).strip()
    snippet = raw_snippet[:120]
    attach  = " 📎" if email.get("has_attachments") else ""

    return (
        f"*{index}.* {subject}{attach}\n"
        f"From: {sender}\n"
        f"Date: {date_str}\n"
        f"_{snippet}..._"
    )


async def handle_emails_command(chat_id: int, text: str, students: list[dict]):
    """
    Handle all /emails subcommands:
      /emails           → last 5
      /emails 10        → last 10
      /emails from ajay → filter by sender
      /emails date 2026-06-01
      /emails subject assignment
    """
    from app.db.supabase import get_emails

    # Find the student with Gmail data (personal account)
    gmail_student = next(
        (s for s in students if any("gmail" in sc for sc in
         (s.get("google_tokens") or {}).get("scopes", []))),
        students[0]
    )
    student_id = gmail_student["id"]

    args = text[len("/emails"):].strip()
    limit   = 5
    date    = None
    sender  = None
    subject = None

    if args.isdigit():
        limit = min(int(args), 20)
    elif args.startswith("from "):
        sender = args[5:].strip()
        limit  = 10
    elif args.startswith("date "):
        date  = args[5:].strip()
        limit = 20
    elif args.startswith("subject "):
        subject = args[8:].strip()
        limit   = 10

    emails = await get_emails(
        student_id=student_id,
        limit=limit,
        date=date,
        sender=sender,
        subject=subject,
    )

    if not emails:
        await send_message(chat_id, "No emails found matching your query.")
        return

    label = ""
    if sender:   label = f" from *{sender}*"
    if date:     label = f" on *{date}*"
    if subject:  label = f" with subject *{subject}*"

    header = f"📬 *{len(emails)} email(s){label}:*\n\n"
    blocks = [_format_email_for_telegram(e, i+1) for i, e in enumerate(emails)]
    msg = header + "\n\n".join(blocks)

    # Telegram message limit is 4096 chars
    if len(msg) > 4000:
        msg = msg[:3990] + "\n\n_...truncated_"

    await send_message(chat_id, msg)


async def handle_message(chat_id: int, text: str):
    text = text.strip()

    if text.startswith("/start"):
        students = await get_students(chat_id)
        if students:
            name = students[0]["name"].split()[0]
            accounts = ", ".join(s["email"] for s in students)
            await send_message(chat_id, f"👋 Welcome back, {name}!\nLinked accounts: {accounts}\n\n{HELP_TEXT}")
        else:
            await send_message(chat_id, "Please login at the web app first to link your account.")
        return

    students = await get_students(chat_id)
    if not students:
        await send_message(chat_id, "Please login at the web app first.")
        return

    # Primary student = IIT account (has Classroom/Calendar tokens)
    primary = students[0]

    if text == "/help":
        await send_message(chat_id, HELP_TEXT)

    elif text == "/deadlines":
        from app.alerts.digest import build_digest
        msg = await build_digest(primary)
        await send_message(chat_id, msg)

    elif text == "/sync":
        await send_message(chat_id, "🔄 Syncing... (~30s)")
        from app.connectors.classroom import sync_student_classroom
        from app.connectors.calendar_conn import sync_student_calendar
        from app.connectors.gmail_conn import sync_student_gmail
        from app.intelligence.pipeline import process_student_items
        for student in students:
            await sync_student_gmail(student)
            await process_student_items(student)
        await sync_student_classroom(primary)
        await sync_student_calendar(primary)
        await process_student_items(primary)
        await send_message(chat_id, "✅ Sync complete!")

    elif text.startswith("/emails"):
        await handle_emails_command(chat_id, text, students)

    elif text.startswith("/ask ") or (not text.startswith("/") and len(text) > 3):
        question = text.removeprefix("/ask").strip()
        await send_message(chat_id, "🤔 Thinking...")
        from app.rag.generator import answer_question
        # Search across ALL linked accounts (Classroom + Calendar + Gmail)
        all_ids = [s["id"] for s in students]
        result = await answer_question(question=question, student_id=all_ids)
        await send_message(chat_id, result["answer"])

    else:
        await send_message(chat_id, "Type /help to see available commands.")


async def poll():
    from telegram import Bot
    bot = Bot(token=settings.telegram_bot_token)
    offset = 0
    print("🤖 Telegram bot polling started. Send a message to @studentai_iitdh_bot")
    print("   Press Ctrl+C to stop.\n")

    while True:
        try:
            updates = await bot.get_updates(offset=offset, timeout=20, allowed_updates=["message"])
            for update in updates:
                offset = update.update_id + 1
                if update.message and update.message.text:
                    chat_id = update.message.chat.id
                    text = update.message.text
                    print(f"← [{update.message.from_user.first_name}] {text}")
                    await handle_message(chat_id, text)
        except Exception as e:
            print(f"Poll error: {e}")
            await asyncio.sleep(3)


if __name__ == "__main__":
    asyncio.run(poll())
