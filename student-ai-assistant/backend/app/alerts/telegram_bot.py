"""
Telegram sender.

Telegram is the delivery channel because it is free, needs no approval queue,
and is already installed on every phone in an Indian college batch. WhatsApp
Business would cost per conversation and require Meta review.
"""

import asyncio
import logging

from telegram import Bot
from telegram.constants import ParseMode
from telegram.error import BadRequest, Forbidden, RetryAfter, TelegramError

from app.config import settings

logger = logging.getLogger(__name__)

_bot: Bot | None = None
MAX_MESSAGE_CHARS = 4096


def get_bot() -> Bot:
    global _bot
    if _bot is None:
        if not settings.telegram_bot_token:
            raise RuntimeError("TELEGRAM_BOT_TOKEN is not set")
        _bot = Bot(token=settings.telegram_bot_token)
    return _bot


async def send_message(chat_id: int, text: str, markdown: bool = True) -> bool:
    """
    Send one message. Returns whether it was delivered; never raises.

    Markdown failures fall back to plain text rather than dropping the message.
    An unescaped '*' in a professor's subject line should not cost a student
    their reminder.
    """
    if not text.strip():
        return False

    if len(text) > MAX_MESSAGE_CHARS:
        text = text[: MAX_MESSAGE_CHARS - 20].rsplit("\n", 1)[0] + "\n…"

    try:
        await get_bot().send_message(
            chat_id=chat_id,
            text=text,
            parse_mode=ParseMode.MARKDOWN if markdown else None,
            disable_web_page_preview=True,
        )
        return True

    except BadRequest as exc:
        if markdown and "parse" in str(exc).lower():
            logger.warning("Markdown parse failed for %s, retrying plain: %s", chat_id, exc)
            return await send_message(chat_id, text, markdown=False)
        logger.error("Telegram rejected message to %s: %s", chat_id, exc)
        return False

    except Forbidden:
        # The student blocked the bot or deleted the chat. Expected, not an error.
        logger.info("Chat %s has blocked the bot — skipping", chat_id)
        return False

    except RetryAfter as exc:
        wait = min(int(exc.retry_after) + 1, 30)
        logger.warning("Telegram rate limit, waiting %ss", wait)
        await asyncio.sleep(wait)
        try:
            await get_bot().send_message(
                chat_id=chat_id,
                text=text,
                parse_mode=ParseMode.MARKDOWN if markdown else None,
                disable_web_page_preview=True,
            )
            return True
        except TelegramError as retry_exc:
            logger.error("Telegram retry failed for %s: %s", chat_id, retry_exc)
            return False

    except TelegramError as exc:
        logger.error("Telegram send failed for %s: %s", chat_id, exc)
        return False


async def send_deadline_alert(
    chat_id: int, title: str, deadline_str: str, alert_type: str, source: str = ""
) -> bool:
    emoji = {"deadline_6h": "🚨", "deadline_24h": "⏰", "deadline_48h": "📌"}.get(alert_type, "📌")
    urgency = {
        "deadline_6h": "*Due in a few hours*",
        "deadline_24h": "*Due tomorrow*",
        "deadline_48h": "*Coming up*",
    }.get(alert_type, "*Reminder*")

    safe_title = title.replace("_", "\\_").replace("*", "\\*").replace("`", "\\`")
    text = (
        f"{emoji} {urgency}\n\n"
        f"*{safe_title}*\n"
        f"⏳ {deadline_str}\n"
        + (f"_via {source}_\n" if source else "")
        + "\nNeed details? `/ask about this`"
    )
    return await send_message(chat_id, text)


async def send_digest(chat_id: int, digest_text: str) -> bool:
    return await send_message(chat_id, digest_text)


async def set_webhook(webhook_url: str) -> bool:
    try:
        await get_bot().set_webhook(
            url=webhook_url,
            secret_token=settings.telegram_webhook_secret or None,
            allowed_updates=["message"],
            drop_pending_updates=True,
        )
        logger.info("Telegram webhook registered: %s", webhook_url)
        return True
    except TelegramError as exc:
        logger.error("Webhook registration failed: %s", exc)
        return False


async def delete_webhook() -> bool:
    """Required before local polling — Telegram allows one or the other."""
    try:
        await get_bot().delete_webhook(drop_pending_updates=False)
        return True
    except TelegramError as exc:
        logger.error("Webhook deletion failed: %s", exc)
        return False
