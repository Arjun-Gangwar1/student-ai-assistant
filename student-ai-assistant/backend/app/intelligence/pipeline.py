"""
Intelligence pipeline: runs classification + extraction + embedding
on raw items that haven't been processed yet.
Called by the background worker after each connector sync.
"""

import asyncio
import logging
from datetime import datetime

from app.db.supabase import get_unprocessed_items, update_item, upsert_deadline, get_supabase
from app.intelligence.classifier import classify_item
from app.intelligence.extractor import extract_deadlines
from app.intelligence.embedder import embed_text

logger = logging.getLogger(__name__)


async def process_student_items(student: dict) -> int:
    """
    Classify, extract deadlines from, and embed all unprocessed items
    for a student. Returns number of items processed.
    """
    student_id = student["id"]
    year = student.get("year")
    branch = student.get("branch")

    raw_items = await get_unprocessed_items(student_id)
    if not raw_items:
        return 0

    count = 0
    for item in raw_items:
        try:
            text = item.get("raw_content", "")
            if not text.strip():
                continue

            # Step 1: Classify
            classification = await classify_item(text, year=year, branch=branch)

            # Step 2: Extract deadlines (website scrapes only — not gmail/classroom/calendar)
            # Gmail emails rarely have clean extractable deadlines; skip to save Groq quota
            new_deadlines = []
            if item.get("source") == "website":
                new_deadlines = await extract_deadlines(text)

            # Step 3: Embed
            embed_text_input = f"{item.get('title', '')} {classification['summary']} {text[:500]}"
            vector = await embed_text(embed_text_input)

            # Step 4: Update item record
            update_data = {
                "category": classification["category"],
                "priority": classification["priority"],
                "relevance_score": classification["relevance_score"],
                "summary": classification["summary"] or item.get("title", ""),
                "embedding": vector,
            }

            # Use first extracted deadline if item has none (and it's a valid timestamp)
            if new_deadlines and not item.get("deadline"):
                best = new_deadlines[0]
                due = best.get("due_at")
                if due and due != "null" and due is not None:
                    update_data["deadline"] = due
                    update_data["confidence"] = best["confidence"]

            await update_item(item["id"], update_data)

            # Step 5: Persist extracted deadlines
            for dl in new_deadlines:
                await upsert_deadline({
                    "student_id": student_id,
                    "item_id": item["id"],
                    "title": dl["title"],
                    "due_at": dl["due_at"],
                    "source": item["source"],
                    "confirmed": dl["confirmed"],
                })

            count += 1
            logger.debug(f"Processed item {item['id']} → {classification['category']}/{classification['priority']}")

            # Groq free tier: 30 req/min → 2s between calls
            await asyncio.sleep(2)

        except Exception as e:
            logger.error(f"Failed to process item {item.get('id')}: {e}")
            await asyncio.sleep(2)  # back off on error
            continue

    logger.info(f"Intelligence pipeline: processed {count} items for student {student_id}")
    return count
