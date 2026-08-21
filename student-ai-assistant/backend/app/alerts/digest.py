"""
Daily morning digest builder.
Runs at configured digest_time (default 07:30 IST) for each student.
"""

import logging
from datetime import datetime

from app.db.supabase import get_upcoming_deadlines, get_supabase
from app.utils.date_utils import format_deadline_for_telegram, hours_until, to_ist

logger = logging.getLogger(__name__)


async def build_digest(student: dict) -> str:
    """
    Build the morning digest message for a student.
    Returns a Markdown-formatted string for Telegram.
    """
    name = student.get("name", "Student")
    student_id = student["id"]

    deadlines = await get_upcoming_deadlines(student_id, days=7)

    # Fetch top 3 high-priority unread items
    db = get_supabase()
    hp_res = (
        db.table("items")
        .select("title, summary, source, category")
        .eq("student_id", student_id)
        .eq("is_read", False)
        .eq("priority", "HIGH")
        .order("created_at", desc=True)
        .limit(3)
        .execute()
    )
    high_items = hp_res.data or []

    ist_now = to_ist(datetime.utcnow())
    date_str = ist_now.strftime("%A, %d %B")

    lines = [f"☀️ *Good morning, {name}!*\n_{date_str}_\n"]

    # ── Deadlines section ─────────────────────────────────────────────────
    if deadlines:
        lines.append("📌 *Upcoming Deadlines:*")
        for d in deadlines[:6]:
            title = d.get("title", "Untitled")
            due_str_raw = d.get("due_at", "")
            try:
                due_dt = datetime.fromisoformat(due_str_raw)
                h = hours_until(due_dt)
                emoji = "🔴" if h <= 24 else "🟡" if h <= 72 else "🟢"
                due_str = format_deadline_for_telegram(due_dt)
            except Exception:
                emoji = "🔵"
                due_str = due_str_raw[:16]
            lines.append(f"{emoji} {title}\n   ↳ {due_str}")
    else:
        lines.append("✅ *No deadlines in the next 7 days!*")

    # ── High-priority items ───────────────────────────────────────────────
    if high_items:
        lines.append("\n⚡ *Action Required:*")
        for item in high_items:
            summary = item.get("summary") or item.get("title", "")
            source = item.get("source", "")
            lines.append(f"• {summary} _[{source}]_")

    # ── Footer ────────────────────────────────────────────────────────────
    lines.append(
        "\n💬 Ask me anything:\n"
        "`/ask what assignments are due this week?`\n"
        "`/ask what is today's mess menu?`"
    )

    return "\n".join(lines)


async def send_all_digests() -> int:
    """Send morning digest to every student who has a Telegram chat ID."""
    from app.db.supabase import get_all_students
    from app.alerts.telegram_bot import send_digest

    students = await get_all_students()
    sent = 0

    for student in students:
        chat_id = student.get("telegram_chat_id")
        if not chat_id:
            continue
        try:
            digest_text = await build_digest(student)
            success = await send_digest(int(chat_id), digest_text)
            if success:
                sent += 1
        except Exception as e:
            logger.error(f"Digest failed for student {student['id']}: {e}")

    logger.info(f"Sent {sent}/{len(students)} morning digests")
    return sent
