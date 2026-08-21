"""
Alert engine: runs periodic checks for deadlines needing 48h/24h/6h alerts.
Called by the background worker every 30 minutes.
"""

import logging
from app.db.supabase import get_deadlines_needing_alert, mark_alert_sent, log_alert
from app.alerts.telegram_bot import send_deadline_alert
from app.utils.date_utils import format_deadline_for_telegram, to_ist
from datetime import datetime

logger = logging.getLogger(__name__)

ALERT_CHECKS = ["alert_sent_48h", "alert_sent_24h", "alert_sent_6h"]


async def run_deadline_alerts() -> int:
    """Check and send all pending deadline alerts. Returns total alerts sent."""
    total_sent = 0

    for alert_field in ALERT_CHECKS:
        deadlines = await get_deadlines_needing_alert(alert_field)

        for dl in deadlines:
            student_info = dl.get("students", {})
            chat_id = student_info.get("telegram_chat_id")
            if not chat_id:
                # Mark as sent anyway to avoid repeated checks
                await mark_alert_sent(dl["id"], alert_field)
                continue

            try:
                due_dt = datetime.fromisoformat(dl["due_at"])
                deadline_str = format_deadline_for_telegram(due_dt)

                success = await send_deadline_alert(
                    chat_id=int(chat_id),
                    title=dl["title"],
                    deadline_str=deadline_str,
                    alert_type=alert_field.replace("alert_sent_", "deadline_"),
                )

                await mark_alert_sent(dl["id"], alert_field)

                if success:
                    await log_alert({
                        "student_id": dl["student_id"],
                        "deadline_id": dl["id"],
                        "channel": "telegram",
                        "alert_type": alert_field,
                    })
                    total_sent += 1

            except Exception as e:
                logger.error(f"Alert send failed for deadline {dl['id']}: {e}")

    logger.info(f"Alert engine: sent {total_sent} alerts")
    return total_sent
