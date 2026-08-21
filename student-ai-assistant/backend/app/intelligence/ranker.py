"""
Re-ranks retrieved items by how much they matter to the student right now.

Retrieval scores textual similarity. This adds what the database cannot know:
that a deadline six hours away outranks a marginally better textual match from
three weeks ago.
"""

from datetime import datetime

from app.utils.date_utils import ensure_aware, now_utc

PRIORITY_WEIGHTS = {"HIGH": 3.0, "MEDIUM": 1.5, "LOW": 0.6}

# Half-life in hours for content recency. One week: a notice from last Monday is
# worth about half a notice from today, which matches how fast campus
# information goes stale.
RECENCY_HALFLIFE_HOURS = 168.0


def _urgency_multiplier(deadline: datetime | None, now: datetime) -> float:
    """
    Sharply favour imminent deadlines.

    Overdue items keep a mild boost rather than being buried: a student who
    missed something usually still needs to see it.
    """
    if deadline is None:
        return 1.0

    hours_left = (ensure_aware(deadline) - now).total_seconds() / 3600

    if hours_left < -48:
        return 0.3
    if hours_left < 0:
        return 1.2
    if hours_left <= 6:
        return 4.0
    if hours_left <= 24:
        return 2.5
    if hours_left <= 72:
        return 1.6
    if hours_left <= 168:
        return 1.2
    return 1.0


def _recency_weight(created_at: datetime | None, now: datetime) -> float:
    if created_at is None:
        return 0.6
    age_hours = max(0.0, (now - ensure_aware(created_at)).total_seconds() / 3600)
    return max(0.1, 0.5 ** (age_hours / RECENCY_HALFLIFE_HOURS))


def score_item(item: dict, now: datetime | None = None) -> float:
    now = now or now_utc()

    try:
        relevance = float(item.get("relevance_score") or 0.5)
    except (TypeError, ValueError):
        relevance = 0.5

    priority_weight = PRIORITY_WEIGHTS.get(item.get("priority") or "LOW", 0.6)
    recency = _recency_weight(item.get("created_at"), now)
    urgency = _urgency_multiplier(item.get("deadline"), now)

    # Retrieval's own opinion, when the query produced one. Preserved so a
    # strong textual match is not swamped by a merely recent item.
    try:
        retrieval = float(item.get("rrf_score") or item.get("similarity") or 0.0)
    except (TypeError, ValueError):
        retrieval = 0.0
    retrieval_boost = 1.0 + min(1.0, retrieval * 10)

    # Unread items edge ahead of ones already seen.
    unread_boost = 1.15 if not item.get("is_read", False) else 1.0

    return relevance * priority_weight * recency * urgency * retrieval_boost * unread_boost


def rank_items(items: list[dict], top_k: int = 10) -> list[dict]:
    """Return the top_k items by score, each annotated with `_score`."""
    if not items:
        return []

    now = now_utc()
    scored = [{**item, "_score": score_item(item, now)} for item in items]
    scored.sort(key=lambda i: i["_score"], reverse=True)
    return scored[:top_k]
