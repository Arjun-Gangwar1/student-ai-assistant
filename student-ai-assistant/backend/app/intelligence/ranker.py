"""
Relevance ranker: combines recency + priority + relevance_score.
Used to sort items for digest and Q&A context selection.
"""

from datetime import datetime, timezone
from typing import Any


def rank_items(items: list[dict], top_k: int = 10) -> list[dict]:
    """
    Score and rank items. Higher score = more important to student right now.
    Score = relevance_score * priority_weight * recency_weight
    """
    priority_weights = {"HIGH": 3.0, "MEDIUM": 1.5, "LOW": 0.5}
    now = datetime.now(timezone.utc)

    scored = []
    for item in items:
        priority = item.get("priority", "LOW")
        relevance = float(item.get("relevance_score", 0.5))
        pw = priority_weights.get(priority, 0.5)

        # Recency: items from last 24h get full weight, older items decay
        created_str = item.get("created_at", "")
        recency_w = 1.0
        if created_str:
            try:
                created = datetime.fromisoformat(created_str)
                if created.tzinfo is None:
                    created = created.replace(tzinfo=timezone.utc)
                age_hours = (now - created).total_seconds() / 3600
                recency_w = max(0.1, 1.0 - (age_hours / 168))  # decay over 7 days
            except ValueError:
                pass

        # Deadline urgency boost
        dl_str = item.get("deadline")
        urgency_boost = 1.0
        if dl_str:
            try:
                dl = datetime.fromisoformat(dl_str)
                if dl.tzinfo is None:
                    dl = dl.replace(tzinfo=timezone.utc)
                hours_left = (dl - now).total_seconds() / 3600
                if 0 < hours_left <= 6:
                    urgency_boost = 4.0
                elif 0 < hours_left <= 24:
                    urgency_boost = 2.5
                elif 0 < hours_left <= 72:
                    urgency_boost = 1.5
            except ValueError:
                pass

        score = relevance * pw * recency_w * urgency_boost
        scored.append({**item, "_score": score})

    scored.sort(key=lambda x: x["_score"], reverse=True)
    return scored[:top_k]
