
"""
Morning digest.

Two behavioural fixes over the original: it honours each student's own
`digest_time` instead of one global cron hour, and it will not send twice in a
day even if the scheduler double-fires — enforced by a unique index, not by
hoping.

The digest is the retention mechanism. The plan gates Phase 2 on a ≥30% open
rate, so its content has to be worth opening: concrete deadlines with time
remaining, not a summary of how many notifications arrived.
"""

import logging

from app.db import queries
from app.utils.date_utils import (
    format_deadline_for_telegram,
    hours_until,
    now_ist,
    to_ist,
)

logger = logging.getLogger(__name__)

MAX_DEADLINES_SHOWN = 6
MAX_ACTION_ITEMS = 3


def _escape_markdown(text: str) -> str:
    """
    Escape Telegram legacy-Markdown control characters.

    Unescaped, a subject line containing a stray '*' or '_' makes the whole
    message fail to send with 400 "can't parse entities" — and the student
    simply gets no digest that day, with nothing obviously broken in the logs.
    """
    for char in ("_", "*", "`", "["):
        text = text.replace(char, f"\\{char}")
    return text


async def build_digest(student: dict) -> str | None:
    """
    Build a student's digest, or None when there is nothing worth sending.

    A digest that says "nothing to report" every morning trains people to
    ignore it, which costs more than the missed touchpoint gains.
    """
    student_id = str(student["id"])
    name = (student.get("name") or "there").split()[0]

    deadlines = await queries.get_upcoming_deadlines(student_id, days=7)
    high_priority = await queries.get_high_priority_items(student_id, limit=MAX_ACTION_ITEMS)

    if not deadlines and not high_priority:
        return None

    today = now_ist()
    lines = [
        f"☀️ *Good morning, {_escape_markdown(name)}*",
        f"_{today.strftime('%A, %d %B')}_",
        "",
    ]

    if deadlines:
        due_today = [d for d in deadlines if to_ist(d["due_at"]).date() == today.date()]
        header = (
            f"🔴 *{len(due_today)} due today*"
            if due_today
            else f"📌 *{len(deadlines)} deadline{'s' if len(deadlines) != 1 else ''} this week*"
        )
        lines.append(header)

        for deadline in deadlines[:MAX_DEADLINES_SHOWN]:
            hours = hours_until(deadline["due_at"])
            marker = "🔴" if hours <= 24 else "🟡" if hours <= 72 else "🟢"
            title = _escape_markdown(deadline["title"][:70])
            when = format_deadline_for_telegram(deadline["due_at"])
            # Flag anything the student has not confirmed, so an AI-extracted
            # date is never presented with the same authority as a Classroom one.
            unconfirmed = "" if deadline["confirmed"] else "  ⚠️ _unconfirmed_"
            lines.append(f"{marker} {title}\n   ↳ {when}{unconfirmed}")

        if len(deadlines) > MAX_DEADLINES_SHOWN:
            lines.append(f"   _+{len(deadlines) - MAX_DEADLINES_SHOWN} more_")
        lines.append("")

    if high_priority:
        lines.append("⚡ *Needs attention*")
        for item in high_priority:
            summary = _escape_markdown((item.get("summary") or item.get("title") or "")[:90])
            lines.append(f"• {summary}  _[{item.get('source', '')}]_")
        lines.append("")

    lines.append("💬 `/ask what's due this week?`")
    return "\n".join(lines)


async def send_due_digests() -> int:
    """
    Send to every student whose digest_time has passed within the last 15
    minutes and who has not already received one today.

    Called every 15 minutes, so each student receives theirs within a quarter
    hour of their chosen time.
    """
    from app.alerts.telegram_bot import send_message

    try:
        students = await queries.get_active_students()
    except Exception as exc:
        logger.error("Digest: could not load students: %s", exc)
        return 0

    now = now_ist()
    now_minutes = now.hour * 60 + now.minute
    sent = 0

    for student in students:
        chat_id = student.get("telegram_chat_id")
        if not chat_id:
            continue

        digest_time = student.get("digest_time")
        if digest_time is None:
            continue
        target_minutes = digest_time.hour * 60 + digest_time.minute

        # Window rather than equality: the job ticks every 15 minutes and will
        # not land exactly on 07:30.
        if not (0 <= now_minutes - target_minutes < 15):
            continue

        student_id = str(student["id"])
        if await queries.digest_already_sent_today(student_id):
            continue

        try:
            digest = await build_digest(student)
            if digest is None:
                logger.debug("Nothing to send for %s", student_id)
                continue

            delivered = await send_message(int(chat_id), digest)
            # Log before checking delivery: the row is what prevents a retry
            # loop from sending the same digest repeatedly.
            await queries.log_alert(
                student_id=student_id,
                alert_type="digest",
                channel="telegram",
                delivered=delivered,
            )
            if delivered:
                sent += 1
        except Exception as exc:
            logger.error("Digest failed for %s: %s", student_id, exc)

    if sent:
        logger.info("Digest: sent %d", sent)
    return sent
