"""
Telegram Bot sender.
Uses python-telegram-bot to push messages to students.
Handles markdown formatting, rate limiting, and error logging.
"""

import logging
from telegram import Bot
from telegram.constants import ParseMode
from telegram.error import TelegramError
from tenacity import retry, stop_after_attempt, wait_exponential

from app.config import settings

logger = logging.getLogger(__name__)

_bot: Bot | None = None


def get_bot() -> Bot:
    global _bot
    if _bot is None:
        _bot = Bot(token=settings.telegram_bot_token)
    return _bot


@retry(stop=stop_after_attempt(3), wait=wait_exponential(min=2, max=15))
async def send_message(chat_id: int, text: str) -> bool:
    """Send a plain or markdown message to a Telegram chat."""
    try:
        await get_bot().send_message(
            chat_id=chat_id,
            text=text,
            parse_mode=ParseMode.MARKDOWN,
            disable_web_page_preview=True,
        )
        return True
    except TelegramError as e:
        logger.error(f"Telegram send failed for {chat_id}: {e}")
        return False


async def send_deadline_alert(chat_id: int, title: str, deadline_str: str, alert_type: str) -> bool:
    emoji_map = {
        "deadline_6h":  "🚨",
        "deadline_24h": "⏰",
        "deadline_48h": "📌",
    }
    emoji = emoji_map.get(alert_type, "📌")
    text = (
        f"{emoji} *Deadline Reminder*\n\n"
        f"*{title}*\n"
        f"Due: {deadline_str}\n\n"
        f"_Tap /ask if you need help with this._"
    )
    return await send_message(chat_id, text)


async def send_digest(chat_id: int, digest_text: str) -> bool:
    return await send_message(chat_id, digest_text)


async def set_webhook(webhook_url: str) -> bool:
    """Register the Telegram webhook URL with Bot API."""
    try:
        await get_bot().set_webhook(
            url=webhook_url,
            secret_token=settings.telegram_webhook_secret,
            allowed_updates=["message"],
        )
        logger.info(f"Telegram webhook set to {webhook_url}")
        return True
    except TelegramError as e:
        logger.error(f"Failed to set webhook: {e}")
        return False
