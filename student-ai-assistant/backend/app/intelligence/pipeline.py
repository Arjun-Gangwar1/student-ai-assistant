"""
Intelligence pipeline: classify → extract → embed → persist.

Reworked for throughput. The previous version slept 2 seconds after every item
to stay inside Groq's free-tier rate limit, serially, inside a job that ran every
three minutes. One student with 50 new emails occupied the worker for over 100
seconds of pure sleep, and with `max_instances=1` the next run was silently
dropped rather than queued.

Now:
  * Classification runs concurrently, bounded by a semaphore sized to the rate
    limit, so throughput is limited by the quota rather than by latency.
  * Embeddings are batched — sentence-transformers encodes 32 texts in barely
    more time than one, and it is a local model with no quota at all.
  * Deadline extraction runs only where deadlines plausibly hide, which is what
    keeps the LLM budget inside the plan's ~₹2-5/user/month.
"""

import asyncio
import logging
import re

from app.db import queries
from app.intelligence.classifier import classify_item
from app.intelligence.embedder import embed_batch
from app.intelligence.extractor import extract_deadlines
from app.utils import token_budget

logger = logging.getLogger(__name__)

# Groq's free tier allows ~30 requests/minute. Six concurrent classifications
# with ~1s latency each lands near 30 rpm without bursting past it.
MAX_CONCURRENT_LLM = 6
EMBED_BATCH_SIZE = 32

# Sources whose text is prose that may bury a date in a sentence. Classroom and
# Calendar deliver structured due dates already, so paying for extraction there
# would be spending tokens to re-derive a field the API handed us.
EXTRACTION_SOURCES = {"website", "gmail", "gmail_attachment", "telegram"}

# Cheap pre-filter: no date-like token, no LLM call. Roughly two thirds of
# emails never reach the extractor.
DATE_HINT_RE = re.compile(
    r"\b(?:"
    r"\d{1,2}[/\-.]\d{1,2}(?:[/\-.]\d{2,4})?"                       # 18/02/2026
    r"|\d{1,2}\s*(?:st|nd|rd|th)?\s*"
    r"(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)"          # 18 Feb
    r"|(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)\w*\s+\d{1,2}"
    r"|(?:mon|tues|wednes|thurs|fri|satur|sun)day"
    r"|deadline|due\s+(?:by|on|date)|last\s+date|submit\s+by"
    r"|before\s+\d|closes?\s+on|register\s+by|cut[\s-]?off"
    r"|tomorrow|tonight|kal\b|aaj\b"
    r")\b",
    re.IGNORECASE,
)


def _deadline_dedup_key(item: dict, title: str) -> str:
    """Stable identity for a deadline derived from an item's text."""
    slug = re.sub(r"[^a-z0-9]+", "-", title.lower())[:40].strip("-")
    return f"extracted:{item['id']}:{slug}"


async def process_student_items(student: dict, limit: int = 50) -> int:
    """
    Process every unprocessed item for one student.
    Returns the number successfully processed.
    """
    student_id = str(student["id"])
    year, branch = student.get("year"), student.get("branch")

    items = await queries.get_unprocessed_items(student_id, limit=limit)
    if not items:
        return 0

    logger.info("Pipeline: %d unprocessed item(s) for %s", len(items), student_id)

    # ── 1. Classify, concurrently but rate-bounded ───────────────────────────
    semaphore = asyncio.Semaphore(MAX_CONCURRENT_LLM)

    async def classify_one(item: dict) -> dict:
        if not token_budget.allow_background():
            raise RuntimeError("daily token budget reserved for chat — deferred to next run")
        async with semaphore:
            return await classify_item(
                raw_content=item.get("raw_content", ""),
                title=item.get("title"),
                year=year,
                branch=branch,
            )

    classifications = await asyncio.gather(
        *(classify_one(item) for item in items), return_exceptions=True
    )

    # ── 2. Extract deadlines where they plausibly exist ──────────────────────
    async def extract_one(item: dict) -> list[dict]:
        text = item.get("raw_content", "")
        if item.get("source") not in EXTRACTION_SOURCES:
            return []
        if item.get("deadline"):
            return []          # source already gave us an authoritative date
        if not DATE_HINT_RE.search(text):
            return []
        if not token_budget.allow_background():
            return []          # classify_one already deferred this item; skip extraction too
        async with semaphore:
            return await extract_deadlines(text, source=item.get("source", "unknown"))

    extractions = await asyncio.gather(
        *(extract_one(item) for item in items), return_exceptions=True
    )

    # ── 3. Embed in batches (local model, no quota) ──────────────────────────
    embed_inputs: list[str] = []
    for item, classification in zip(items, classifications):
        summary = classification.get("summary", "") if isinstance(classification, dict) else ""
        embed_inputs.append(
            f"{item.get('title') or ''}\n{summary}\n{item.get('raw_content', '')[:600]}".strip()
        )

    vectors: list[list[float] | None] = []
    for start in range(0, len(embed_inputs), EMBED_BATCH_SIZE):
        chunk = embed_inputs[start : start + EMBED_BATCH_SIZE]
        try:
            vectors.extend(await embed_batch(chunk))
        except Exception as exc:
            # An item without an embedding is still useful — full-text search
            # will find it — so record the failure and carry on.
            logger.error("Embedding batch failed: %s", exc)
            vectors.extend([None] * len(chunk))

    # ── 4. Persist ───────────────────────────────────────────────────────────
    processed = 0
    for item, classification, extracted, vector in zip(items, classifications, extractions, vectors):
        item_id = str(item["id"])

        if isinstance(classification, BaseException):
            logger.error("Classification failed for item %s: %s", item_id, classification)
            continue
        if isinstance(extracted, BaseException):
            logger.error("Extraction failed for item %s: %s", item_id, extracted)
            extracted = []

        best = max(extracted, key=lambda d: d["confidence"], default=None) if extracted else None

        try:
            await queries.save_item_analysis(
                item_id=item_id,
                category=classification["category"],
                priority=classification["priority"],
                relevance_score=classification["relevance_score"],
                summary=classification["summary"] or (item.get("title") or ""),
                embedding=vector,
                deadline=best["due_at"] if best else None,
                confidence=best["confidence"] if best else None,
            )

            for deadline in extracted:
                await queries.upsert_deadline(
                    student_id=student_id,
                    dedup_key=_deadline_dedup_key(item, deadline["title"]),
                    item_id=item_id,
                    title=deadline["title"],
                    due_at=deadline["due_at"],
                    source=item["source"],
                    confirmed=deadline["confirmed"],
                    confidence=deadline["confidence"],
                )

            processed += 1
        except Exception as exc:
            logger.error("Persisting item %s failed: %s", item_id, exc)

    logger.info("Pipeline: processed %d/%d item(s) for %s", processed, len(items), student_id)
    return processed
