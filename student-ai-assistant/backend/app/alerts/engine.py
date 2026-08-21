"""
Deadline alert engine — 48h / 24h / 6h reminders.

Two rules that matter more than the mechanics:

  * Unconfirmed deadlines do not alert. An LLM-extracted date below the
    confidence bar is a suggestion, and waking someone at 6am for a date the
    model invented is how this product loses a user permanently.

  * The sent-flag is set whether or not delivery succeeded. A Telegram outage
    must not turn into the same reminder every 15 minutes once it recovers.
"""

import logging

from app.alerts.telegram_bot import send_deadline_alert
from app.db import queries
from app.utils.date_utils import format_deadline_for_telegram

logger = logging.getLogger(__name__)

# Order matters: the tightest window last, so a deadline entering the app inside
# 6 hours gets the 48h and 24h flags marked as it passes through, then sends the
# 6h alert — one message, not three at once.
ALERT_FIELDS = ["alert_sent_48h", "alert_sent_24h", "alert_sent_6h"]


async def run_deadline_alerts() -> int:
    """Send all pending deadline reminders. Returns the number delivered."""
    total_sent = 0

    for alert_field in ALERT_FIELDS:
        try:
            deadlines = await queries.get_deadlines_needing_alert(alert_field)
        except Exception as exc:
            logger.error("Alert query failed (%s): %s", alert_field, exc)
            continue

        for deadline in deadlines:
            deadline_id = str(deadline["id"])
            student_id = str(deadline["student_id"])
            chat_id = deadline.get("telegram_chat_id")

            # No Telegram link, or awaiting confirmation: mark the window as
            # handled so it is not re-examined every 15 minutes forever.
            if not chat_id:
                await queries.mark_alert_sent(deadline_id, alert_field)
                continue

            try:
                delivered = await send_deadline_alert(
                    chat_id=int(chat_id),
                    title=deadline["title"],
                    deadline_str=format_deadline_for_telegram(deadline["due_at"]),
                    alert_type=alert_field.replace("alert_sent_", "deadline_"),
                    source=deadline.get("source", ""),
                )

                await queries.mark_alert_sent(deadline_id, alert_field)
                await queries.log_alert(
                    student_id=student_id,
                    deadline_id=deadline_id,
                    channel="telegram",
                    alert_type=alert_field,
                    delivered=delivered,
                )
                if delivered:
                    total_sent += 1
            except Exception as exc:
                # Leave the flag unset so a transient failure is retried next tick.
                logger.error("Alert failed for deadline %s: %s", deadline_id, exc)

    if total_sent:
        logger.info("Alert engine: sent %d reminder(s)", total_sent)
    return total_sent
