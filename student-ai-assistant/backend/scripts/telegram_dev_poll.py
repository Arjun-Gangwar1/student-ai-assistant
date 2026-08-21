#!/usr/bin/env python3
"""
Dev-only Telegram poller.

Telegram allows either a webhook or long polling, never both. In production the
backend registers a webhook; locally there is no public URL, so run this in a
second terminal instead:

    python scripts/telegram_dev_poll.py

Commands are handled by app/alerts/commands.py — the same module the webhook
calls, so local behaviour matches production. Previously the poller had its own
copy of the command handling and the two had already diverged.
"""

import asyncio
import logging
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_DIR))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(BACKEND_DIR / ".env")

from telegram import Update  # noqa: E402

from app.alerts.commands import handle_command  # noqa: E402
from app.alerts.telegram_bot import delete_webhook, get_bot  # noqa: E402
from app.db.pool import close_pool, init_pool  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
logging.getLogger("httpx").setLevel(logging.WARNING)
logger = logging.getLogger("telegram_dev_poll")


async def main() -> None:
    await init_pool()
    bot = get_bot()

    # A registered webhook silently swallows every update, so getUpdates would
    # return nothing and the poller would look broken.
    await delete_webhook()

    me = await bot.get_me()
    print(f"Polling as @{me.username} — Ctrl-C to stop\n")

    offset = None
    try:
        while True:
            try:
                updates = await bot.get_updates(
                    offset=offset, timeout=30, allowed_updates=["message"]
                )
            except Exception as exc:
                logger.error("getUpdates failed: %s", exc)
                await asyncio.sleep(5)
                continue

            for update in updates:
                offset = update.update_id + 1
                message = update.message
                if not message or not message.text:
                    continue

                who = message.chat.username or message.chat.id
                print(f"← [{who}] {message.text}")
                try:
                    await handle_command(chat_id=message.chat.id, text=message.text)
                except Exception as exc:
                    logger.exception("Handler failed: %s", exc)
    except (KeyboardInterrupt, asyncio.CancelledError):
        print("\nStopped.")
    finally:
        await close_pool()


if __name__ == "__main__":
    asyncio.run(main())
