"""
Telegram webhook — command handling for the bot.

Command handling lives in `app/alerts/commands.py` so the webhook (production)
and the dev poller (local) run exactly the same code. Previously the poller had
`/emails` and the webhook did not, so behaviour differed by environment.
"""

import hmac
import logging

from fastapi import APIRouter, Header, HTTPException, Request, status

from app.alerts.commands import handle_command
from app.config import settings

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/telegram", tags=["telegram"])


@router.post("/webhook")
async def telegram_webhook(
    request: Request,
    x_telegram_bot_api_secret_token: str | None = Header(None),
):
    """
    Receive an update from Telegram.

    Always returns 200 for anything that is authentic: a non-2xx makes Telegram
    retry the same update repeatedly, so a bug in one message handler would turn
    into an infinite redelivery loop.
    """
    secret = settings.telegram_webhook_secret
    if secret:
        if not x_telegram_bot_api_secret_token or not hmac.compare_digest(
            x_telegram_bot_api_secret_token, secret
        ):
            logger.warning("Rejected webhook call with a bad secret token")
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Invalid secret token")
    elif settings.is_production:
        # Without a secret anyone who learns the URL can impersonate Telegram.
        logger.error("TELEGRAM_WEBHOOK_SECRET is unset in production — rejecting")
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Webhook secret not configured")

    try:
        update = await request.json()
    except Exception:
        return {"ok": True}

    message = update.get("message") or {}
    chat_id = (message.get("chat") or {}).get("id")
    text = (message.get("text") or "").strip()

    if not chat_id or not text:
        return {"ok": True}

    try:
        await handle_command(chat_id=int(chat_id), text=text)
    except Exception as exc:
        logger.exception("Telegram command failed (chat=%s, text=%r): %s", chat_id, text[:60], exc)

    return {"ok": True}
