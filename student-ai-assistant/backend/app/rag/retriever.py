"""
Retrieval for the Q&A engine.

The previous version issued a vector query and a keyword query from Python and
merged them here. The keyword half fed the raw question to `to_tsquery`, which
raises on spaces and punctuation, so it threw on essentially every real question
and was swallowed by a `try/except` — retrieval had been semantic-only in
practice. Both halves now run inside `hybrid_search_items` and are fused with
Reciprocal Rank Fusion in one round trip.
"""

import logging
from datetime import datetime, timezone

from app.db import queries
from app.intelligence.embedder import embed_text
from app.intelligence.ranker import rank_items

logger = logging.getLogger(__name__)


async def retrieve(
    query: str,
    student_ids: list[str],
    top_k: int = 8,
) -> list[dict]:
    """
    Hybrid retrieval over one or more linked accounts.

    Multiple ids exist because a single Telegram chat may be linked to more than
    one Google account (a personal and an institute address, commonly).
    """
    if not student_ids:
        return []

    try:
        query_vector = await embed_text(query)
    except Exception as exc:
        logger.error("Embedding failed for query %r: %s", query[:60], exc)
        query_vector = None

    try:
        if query_vector is not None:
            rows = await queries.hybrid_search(
                query_text=query,
                query_embedding=query_vector,
                student_ids=student_ids,
                limit=top_k * 2,
            )
        else:
            # Without an embedding, degrade to keyword-only rather than to nothing.
            rows = await queries.search_items_keyword(query, student_ids, limit=top_k * 2)
    except Exception as exc:
        logger.error("Retrieval failed for %r: %s", query[:60], exc)
        return []

    for row in rows:
        row["id"] = str(row["id"])

    # RRF orders by textual match. The re-rank then applies what the database
    # cannot know: that an item due in six hours matters more right now than a
    # slightly better-matching one from three weeks ago.
    return rank_items(rows, top_k=top_k)


def format_context_for_llm(items: list[dict], max_chars: int = 6000) -> str:
    """
    Render retrieved items as numbered context.

    Budgeted by characters: an over-long context both costs tokens and pushes the
    most relevant item (item 1) away from the end of the prompt, where models
    attend most reliably.
    """
    if not items:
        return "No relevant information found in the student's connected accounts."

    now = datetime.now(timezone.utc)
    parts: list[str] = []
    used = 0

    for i, item in enumerate(items, 1):
        source = item.get("source", "unknown")
        title = item.get("title") or item.get("summary") or "Untitled"
        summary = (item.get("summary") or "").strip()
        body = (item.get("raw_content") or "").strip()

        created = item.get("created_at")
        created_str = created.strftime("%d %b %Y") if isinstance(created, datetime) else ""

        lines = [f"[{i}] source: {source}" + (f" | received: {created_str}" if created_str else "")]
        lines.append(f"title: {title}")

        if summary and summary != title:
            lines.append(f"summary: {summary}")

        deadline = item.get("deadline")
        if isinstance(deadline, datetime):
            hours = (deadline - now).total_seconds() / 3600
            when = (
                f"OVERDUE by {abs(int(hours))}h" if hours < 0
                else f"in {int(hours)}h" if hours < 48
                else f"in {int(hours / 24)} days"
            )
            lines.append(f"deadline: {deadline.strftime('%d %b %Y, %I:%M %p IST')} ({when})")

        # A little body text lets the model answer detail questions, but the
        # summary carries most of the signal, so keep the excerpt short.
        if body and body != summary:
            excerpt = body[:400].replace("\n", " ").strip()
            lines.append(f"content: {excerpt}{'…' if len(body) > 400 else ''}")

        chunk = "\n".join(lines)
        if used + len(chunk) > max_chars:
            break
        parts.append(chunk)
        used += len(chunk)

    return "\n---\n".join(parts)
