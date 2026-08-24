"""
Process-wide daily token budget for the shared Groq free-tier key.

Groq's free tier caps tokens per day (TPD), not per user — one key, one pool,
shared by every student's chat AND the background sync pipeline (classification
+ deadline extraction). Without this, a backlog of unprocessed items can burn
the entire day's budget in the background, and every student's chat then fails
with a generic "couldn't reach the AI service" for the rest of the day.

The fix is a reserve: once the remaining budget drops to CHAT_RESERVE, the
background pipeline stops making LLM calls (items just wait for the next run)
so a live chat request always has room. Chat itself checks the budget before
calling, so a request that has no chance of succeeding fails fast and honestly
instead of retrying into a 429.

This is a local estimate, not authoritative — Groq's own counter is server-side
and shared across however this key is used elsewhere. It exists to avoid
*causing* the exhaustion ourselves, not to guarantee it never happens.
"""

import logging
import time

logger = logging.getLogger(__name__)

# Free-tier ceiling for openai/gpt-oss-20b. Matches the number Groq reports in
# its 429 body ("Limit 200000... on tokens per day (TPD)").
DAILY_TOKEN_LIMIT = 200_000

# Kept unspent by background work so an interactive question always has room
# for a real answer (context + question + a ~700 token completion).
CHAT_RESERVE = 40_000

_SECONDS_PER_DAY = 86_400

_day_start = time.time() - (time.time() % _SECONDS_PER_DAY)
_used = 0
_warned_background = False


def _roll_if_new_day() -> None:
    global _day_start, _used, _warned_background
    now = time.time()
    if now - _day_start >= _SECONDS_PER_DAY:
        _day_start = now - (now % _SECONDS_PER_DAY)
        _used = 0
        _warned_background = False


def record(tokens: int) -> None:
    """Add actual (or estimated) token usage from a completed call."""
    global _used
    if tokens <= 0:
        return
    _roll_if_new_day()
    _used += tokens


def remaining() -> int:
    _roll_if_new_day()
    return max(0, DAILY_TOKEN_LIMIT - _used)


def allow_chat() -> bool:
    """Interactive requests get to try until the budget is truly at zero."""
    return remaining() > 0


def allow_background() -> bool:
    """Background jobs stop early, leaving the reserve for live chat."""
    global _warned_background
    ok = remaining() > CHAT_RESERVE
    if not ok and not _warned_background:
        logger.warning(
            "Daily token budget below chat reserve (%d left, reserve %d) — "
            "pausing background LLM calls until tomorrow",
            remaining(),
            CHAT_RESERVE,
        )
        _warned_background = True
    return ok
