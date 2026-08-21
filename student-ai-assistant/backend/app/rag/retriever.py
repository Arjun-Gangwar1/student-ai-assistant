"""
Hybrid retriever: keyword (Postgres FTS) + semantic (pgvector cosine).
Results are merged and re-ranked before passing to the LLM.
"""

import logging
from app.db.supabase import get_supabase
from app.intelligence.embedder import embed_text
from app.intelligence.ranker import rank_items

logger = logging.getLogger(__name__)


async def retrieve(
    query: str,
    student_id: "str | list[str]",
    top_k: int = 8,
) -> list[dict]:
    """
    Hybrid search over items for one or multiple students.
    Pass a list of student_ids to search across linked accounts (e.g. Telegram).
    Returns top_k most relevant items as context for the LLM.
    """
    db = get_supabase()
    student_ids = [student_id] if isinstance(student_id, str) else student_id

    # ── Semantic search ───────────────────────────────────────────────────
    query_vec = await embed_text(query)
    vec_str = "[" + ",".join(str(v) for v in query_vec) + "]"

    semantic_items: list[dict] = []
    for sid in student_ids:
        try:
            semantic_res = db.rpc(
                "match_items",
                {
                    "query_embedding": vec_str,
                    "match_student_id": sid,
                    "match_count": top_k * 2,
                    "similarity_threshold": 0.3,
                },
            ).execute()
            semantic_items.extend(semantic_res.data or [])
        except Exception as e:
            logger.warning(f"Semantic search failed for {sid}: {e}")

    # ── Keyword / FTS search ──────────────────────────────────────────────
    try:
        fts_res = (
            db.table("items")
            .select("*")
            .in_("student_id", student_ids)
            .text_search("title", query)
            .execute()
        )
        fts_items = fts_res.data or []
    except Exception as e:
        logger.warning(f"FTS search failed: {e}")
        fts_items = []

    # ── Merge + dedup ─────────────────────────────────────────────────────
    seen_ids: set[str] = set()
    merged: list[dict] = []
    for item in semantic_items + fts_items:
        if item["id"] not in seen_ids:
            seen_ids.add(item["id"])
            merged.append(item)

    # ── Re-rank by importance + recency ──────────────────────────────────
    ranked = rank_items(merged, top_k=top_k)
    return ranked


def format_context_for_llm(items: list[dict]) -> str:
    """Convert retrieved items into a context string for the LLM prompt."""
    if not items:
        return "No relevant information found."

    parts = []
    for i, item in enumerate(items, 1):
        source = item.get("source", "unknown")
        title = item.get("title") or item.get("summary") or "Untitled"
        summary = item.get("summary", "")
        deadline = item.get("deadline", "")
        created = item.get("created_at", "")[:10]  # date only

        chunk = f"[{i}] Source: {source} | Date: {created}\n"
        chunk += f"Title: {title}\n"
        if summary and summary != title:
            chunk += f"Summary: {summary}\n"
        if deadline:
            chunk += f"Deadline: {deadline}\n"
        parts.append(chunk)

    return "\n---\n".join(parts)
