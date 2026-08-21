"""
Re-embed all items using the new all-mpnet-base-v2 model (768-dim).

Run AFTER the SQL migration (002_emails_structure.sql) which wipes
all existing embeddings. This script only updates the embedding column
— it does NOT re-run classification (Groq), so no rate limits.

Usage:
    cd backend
    source venv/bin/activate
    python scripts/re_embed_all.py
"""

import asyncio
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
logger = logging.getLogger(__name__)


async def main():
    from app.db.supabase import get_supabase
    from app.intelligence.embedder import embed_batch

    db = get_supabase()

    # Fetch all items that need embeddings (NULL after migration)
    logger.info("Fetching items with missing embeddings...")
    res = (
        db.table("items")
        .select("id, title, summary, raw_content, source")
        .is_("embedding", "null")
        .limit(2000)
        .execute()
    )
    items = res.data or []
    logger.info(f"Found {len(items)} items to embed")

    if not items:
        logger.info("Nothing to do.")
        return

    # Build text inputs — same formula used in pipeline.py
    texts = []
    for item in items:
        title   = item.get("title", "") or ""
        summary = item.get("summary", "") or ""
        raw     = (item.get("raw_content", "") or "")[:500]
        texts.append(f"{title} {summary} {raw}".strip())

    # Embed in batches of 32 (memory-safe on CPU)
    BATCH = 32
    total_done = 0

    for i in range(0, len(items), BATCH):
        batch_items = items[i : i + BATCH]
        batch_texts = texts[i : i + BATCH]

        logger.info(f"Embedding batch {i//BATCH + 1} / {(len(items)-1)//BATCH + 1} ...")
        vectors = await embed_batch(batch_texts)

        for item, vector in zip(batch_items, vectors):
            db.table("items").update({"embedding": vector}).eq("id", item["id"]).execute()
            total_done += 1

        logger.info(f"  Done {total_done}/{len(items)}")

    logger.info(f"\nRe-embed complete: {total_done} items updated with 768-dim vectors.")


if __name__ == "__main__":
    asyncio.run(main())
