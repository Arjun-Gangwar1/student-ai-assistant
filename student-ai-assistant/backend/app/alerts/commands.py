"""
Telegram command handling, shared by the webhook and the dev poller.

Previously these two paths had separate implementations and had already drifted:
the poller supported `/emails` with subcommands, the webhook did not. Anything
tested locally was therefore not necessarily what production ran.
"""

import logging
import re
from datetime import datetime, timezone

from app.alerts.digest import build_digest
from app.alerts.telegram_bot import send_message
from app.db import queries
from app.rag.generator import answer_question
from app.utils.date_utils import format_deadline_for_telegram, hours_until
from app.utils.ratelimit import RateLimiter

logger = logging.getLogger(__name__)

# Telegram is the cheapest possible way to spam the LLM, so it gets its own cap.
_ask_limiter = RateLimiter(max_calls=50, window_seconds=24 * 3600, name="telegram_ask")
_sync_limiter = RateLimiter(max_calls=6, window_seconds=3600, name="telegram_sync")

HELP_TEXT = """*Student AI Assistant* 🎓

*/ask* <question> — ask about deadlines, assignments, notices
*/deadlines* — your upcoming deadlines
*/emails* — recent email (see below)
*/sync* — refresh Classroom, Calendar and Gmail
*/help* — this message

*Email filters*
`/emails 10` — last 10
`/emails from ajay` — by sender
`/emails date 2026-08-20` — by date
`/emails subject quiz` — by subject

*Examples*
`/ask what's due this week?`
`/ask kal kya submit karna hai?`

You can also just type a question without `/ask`."""

NOT_LINKED = (
    "👋 Welcome! I'm not connected to your account yet.\n\n"
    "1. Sign in at the web app\n"
    "2. Open *Settings → Telegram*\n"
    "3. Tap the link there\n\n"
    "That connects this chat to your Classroom and Calendar."
)


def _escape(text: str) -> str:
    for char in ("_", "*", "`", "["):
        text = text.replace(char, f"\\{char}")
    return text


async def handle_command(chat_id: int, text: str) -> None:
    """Route one incoming message. Never raises."""
    text = text.strip()
    lowered = text.lower()

    # /start [token] — the only command usable before linking.
    if lowered.startswith("/start"):
        await _handle_start(chat_id, text)
        return

    student = await queries.get_student_by_telegram_chat(chat_id)
    if not student:
        await send_message(chat_id, NOT_LINKED)
        return

    if lowered in ("/help", "help", "/start help"):
        await send_message(chat_id, HELP_TEXT)
    elif lowered.startswith("/deadlines"):
        await _handle_deadlines(chat_id, student)
    elif lowered.startswith("/emails"):
        await _handle_emails(chat_id, student, text)
    elif lowered.startswith("/sync"):
        await _handle_sync(chat_id, student)
    elif lowered.startswith("/ask"):
        await _handle_ask(chat_id, student, text[4:].strip())
    elif text.startswith("/"):
        await send_message(chat_id, "Unknown command. Try /help")
    elif len(text) > 4:
        # Bare text is treated as a question — most people won't type /ask.
        await _handle_ask(chat_id, student, text)
    else:
        await send_message(chat_id, "Type /help to see what I can do.")


async def _handle_start(chat_id: int, text: str) -> None:
    token = text[6:].strip()
    if not token:
        student = await queries.get_student_by_telegram_chat(chat_id)
        await send_message(chat_id, HELP_TEXT if student else NOT_LINKED)
        return

    student = await queries.redeem_telegram_link_token(token, chat_id)
    if student:
        name = _escape((student.get("name") or "").split()[0] if student.get("name") else "")
        await send_message(
            chat_id,
            f"✅ *Connected{', ' + name if name else ''}!*\n\n"
            "You'll get a morning digest and reminders before every deadline.\n\n"
            + HELP_TEXT,
        )
        logger.info("Telegram linked: student=%s chat=%s", student["id"], chat_id)
    else:
        await send_message(
            chat_id,
            "❌ That link has expired or was already used.\n\n"
            "Generate a fresh one in *Settings → Telegram* on the web app.",
        )


async def _handle_deadlines(chat_id: int, student: dict) -> None:
    deadlines = await queries.get_upcoming_deadlines(str(student["id"]), days=14)
    if not deadlines:
        await send_message(chat_id, "🎉 Nothing due in the next 14 days.")
        return

    lines = [f"📅 *{len(deadlines)} upcoming deadline(s)*\n"]
    for deadline in deadlines[:10]:
        hours = hours_until(deadline["due_at"])
        marker = "🔴" if hours <= 24 else "🟡" if hours <= 72 else "🟢"
        flag = "" if deadline["confirmed"] else "  ⚠️ _unconfirmed_"
        lines.append(
            f"{marker} *{_escape(deadline['title'][:70])}*\n"
            f"   {format_deadline_for_telegram(deadline['due_at'])}{flag}"
        )
    if len(deadlines) > 10:
        lines.append(f"\n_+{len(deadlines) - 10} more_")

    await send_message(chat_id, "\n".join(lines))


async def _handle_emails(chat_id: int, student: dict, text: str) -> None:
    student_id = str(student["id"])

    if not student.get("gmail_enabled"):
        await send_message(
            chat_id,
            "📭 Gmail isn't connected. Enable it in *Settings* on the web app.",
        )
        return

    args = text[7:].strip()
    limit, emails = 5, None

    if match := re.match(r"^from\s+(.+)$", args, re.I):
        emails = await queries.list_emails(student_id, limit=10, sender=match.group(1).strip())
        header = f"📬 Email from *{_escape(match.group(1).strip())}*"
    elif match := re.match(r"^date\s+(\d{4}-\d{2}-\d{2})$", args, re.I):
        emails = await queries.list_emails(student_id, limit=20, date=match.group(1))
        header = f"📬 Email on *{match.group(1)}*"
    elif match := re.match(r"^subject\s+(.+)$", args, re.I):
        emails = await queries.list_emails(student_id, limit=10, subject=match.group(1).strip())
        header = f"📬 Subject contains *{_escape(match.group(1).strip())}*"
    elif match := re.match(r"^search\s+(.+)$", args, re.I):
        emails = await queries.search_emails(student_id, match.group(1).strip(), limit=10)
        header = f"🔍 Search: *{_escape(match.group(1).strip())}*"
    else:
        if args.isdigit():
            limit = min(int(args), 20)
        emails = await queries.list_emails(student_id, limit=limit)
        header = f"📬 Last {len(emails or [])} email(s)"

    if not emails:
        await send_message(chat_id, "No matching email found.")
        return

    lines = [header, ""]
    for index, email in enumerate(emails, 1):
        received = email.get("received_at")
        when = received.strftime("%d %b, %I:%M %p") if isinstance(received, datetime) else ""
        sender = email.get("sender_name") or email.get("sender_email") or "Unknown"
        clip = "📎" if email.get("has_attachments") else ""
        snippet = re.sub(r"\s+", " ", (email.get("snippet") or ""))[:100]
        lines.append(
            f"*{index}.* {_escape((email.get('subject') or '(no subject)')[:70])} {clip}\n"
            f"   _{_escape(sender[:40])} · {when}_\n"
            f"   {_escape(snippet)}…"
        )

    await send_message(chat_id, "\n".join(lines))


async def _handle_sync(chat_id: int, student: dict) -> None:
    student_id = str(student["id"])

    allowed, _, retry_after = _sync_limiter.check(student_id)
    if not allowed:
        await send_message(
            chat_id, f"⏳ Too many syncs. Try again in {retry_after // 60 + 1} minutes."
        )
        return

    await send_message(chat_id, "🔄 Syncing… this takes about 30 seconds.")

    from app.workers.sync_worker import sync_one_student

    full = await queries.get_student_with_tokens(student_id)
    if not full or not full.get("google_tokens"):
        await send_message(chat_id, "❌ Google isn't connected. Sign in again on the web app.")
        return

    results = await sync_one_student(full)

    parts = []
    if isinstance(results.get("classroom"), dict):
        parts.append(f"📚 {results['classroom'].get('assignments', 0)} assignment(s)")
    if isinstance(results.get("calendar"), dict):
        parts.append(f"📅 {results['calendar'].get('events', 0)} event(s)")
    if isinstance(results.get("gmail"), dict) and results["gmail"].get("emails"):
        parts.append(f"📬 {results['gmail']['emails']} email(s)")

    summary = "\n".join(parts) if parts else "Nothing new."
    await send_message(chat_id, f"✅ *Sync complete*\n\n{summary}\n\nAsk me anything with /ask")


async def _handle_ask(chat_id: int, student: dict, question: str) -> None:
    if not question:
        await send_message(chat_id, "Ask me something: `/ask what's due this week?`")
        return

    student_id = str(student["id"])
    allowed, _, retry_after = _ask_limiter.check(student_id)
    if not allowed:
        await send_message(
            chat_id, f"⏳ Daily question limit reached. Resets in {retry_after // 3600 + 1}h."
        )
        return

    await send_message(chat_id, "🤔 Thinking…")

    result = await answer_question(
        question=question,
        student_ids=[student_id],
        year=student.get("year"),
        branch=student.get("branch"),
    )

    answer = result["answer"]
    if sources := result.get("sources"):
        names = ", ".join(dict.fromkeys(s["source"] for s in sources if s.get("source")))
        if names:
            answer += f"\n\n_Sources: {names}_"

    await send_message(chat_id, answer, markdown=False)
