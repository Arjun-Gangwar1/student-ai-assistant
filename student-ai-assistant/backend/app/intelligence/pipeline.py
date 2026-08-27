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
from app.intelligence.classifier import BATCH_SIZE, classify_item, classify_items
from app.intelligence.embedder import embed_batch
from app.intelligence.extractor import extract_deadlines
from app.utils import token_budget

logger = logging.getLogger(__name__)

# Groq's free tier allows ~30 requests/minute. Six concurrent classifications
# with ~1s latency each lands near 30 rpm without bursting past it.
MAX_CONCURRENT_LLM = 6
# Peak RSS scales with this, and the spike dwarfs the model itself. Measured
# with 2000-char inputs (what _clean truncates to) on top of a 419MB loaded
# model: batch 32 peaks at 1527MB, batch 16 at 1206MB, batch 8 at 969MB.
# Embedding is not the slow part of a pass -- classification is -- so trading
# throughput here for ~560MB of headroom is nearly free.
EMBED_BATCH_SIZE = 8

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



# Words that describe "this is a deadline" rather than identifying *which*
# deadline. Three emails about the same hackathon called it "application
# deadline", "registration deadline", and just "deadline" — different words,
# same event. Stripping these before slugging collapses all three to one key.
_DEADLINE_FILLER_WORDS = {
    "deadline", "date", "due", "last", "final", "extended",
    "application", "applications", "registration", "registrations",
    "apply", "submit", "submission", "submissions",
    "closes", "closing", "close", "for", "the", "to", "by", "is",
}


def _deadline_dedup_key(deadline: dict) -> str:
    """
    Stable identity for an LLM-extracted deadline, keyed on the deadline's own
    normalised title rather than the item it came from or its exact due time.

    Keying on item_id meant the same real-world event mentioned in three
    separate marketing emails — three different items — produced three
    separate deadline rows. Keying on content instead lets `upsert_deadline`'s
    ON CONFLICT merge them into one, which is what the upsert was already
    built to do (it only exists to avoid duplicate rows across re-syncs — the
    item_id keying just aimed that at the wrong axis).

    Due date is deliberately left out of the key: different emails about the
    same event routinely give slightly different or vaguer due times, and the
    goal is one row that gets refined as later syncs bring a more precise
    date, not one row per phrasing of the deadline. This is safe specifically
    because LLM extraction only runs for prose sources (email, website,
    telegram) — see EXTRACTION_SOURCES; Classroom/Calendar deadlines carry a
    native id and never go through this function, so a genuinely recurring
    assignment (e.g. "Assignment 2" then "Assignment 3") never collapses,
    since its distinguishing number survives filtering.
    """
    words = re.findall(r"[a-z0-9]+", deadline["title"].lower())
    core = [w for w in words if w not in _DEADLINE_FILLER_WORDS]
    slug = "-".join(core or words)[:50]
    return f"extracted:{slug}"


MAX_DRAIN_BATCHES = 12


async def process_student_items(student: dict, limit: int = 50) -> int:
    """
    Process every unprocessed item for one student, draining the backlog in
    batches of `limit`. Returns the number successfully processed.

    Draining matters after a first sync or a restart: one pass used to stop at
    `limit` and wait for the next scheduled run, so a 300-item first sync took
    days to classify on a two-hour Classroom interval.

    A short batch ends the loop. Items that fail stay unprocessed, so the next
    fetch would return the very same rows -- looping on them would spin on a
    failing set and burn quota rather than make progress. Those are left for the
    next scheduled run instead. MAX_DRAIN_BATCHES caps the work either way.
    """
    total = 0
    for _ in range(MAX_DRAIN_BATCHES):
        done = await _process_batch(student, limit)
        total += done
        if done < limit:
            break
    return total


async def _process_batch(student: dict, limit: int) -> int:
    """One pass: classify, extract, embed and persist up to `limit` items."""
    student_id = str(student["id"])
    year, branch = student.get("year"), student.get("branch")

    items = await queries.get_unprocessed_items(student_id, limit=limit)
    if not items:
        return 0

    logger.info("Pipeline: %d unprocessed item(s) for %s", len(items), student_id)

    # ── 1. Classify, in batches ──────────────────────────────────────────────
    #
    # One request per item exhausted the free tier twice over: 350 items cost
    # ~241k tokens against a 200k/day cap, and 350 of the 1000 daily requests,
    # because every call re-sent the same ~1k-character category rules. Ten
    # items per call sends those once and drops the corpus to ~91k tokens and
    # ~35 requests. Concurrency across batches stays bounded as before.
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

    async def classify_batch(batch: list[dict]) -> list[dict | BaseException]:
        if not token_budget.allow_background():
            return [
                RuntimeError("daily token budget reserved for chat — deferred to next run")
            ] * len(batch)
        async with semaphore:
            results = await classify_items(batch, year=year, branch=branch)
        if results is not None:
            return list(results)

        # The batch could not be trusted -- dropped or reordered entries would
        # attach one item's summary to another row. Retry individually so a
        # single malformed response costs accuracy on nothing.
        logger.info("Batch of %d fell back to per-item classification", len(batch))
        return list(
            await asyncio.gather(*(classify_one(item) for item in batch),
                                 return_exceptions=True)
        )

    batches = [items[i : i + BATCH_SIZE] for i in range(0, len(items), BATCH_SIZE)]
    classifications: list[dict | BaseException] = []
    for result in await asyncio.gather(*(classify_batch(b) for b in batches)):
        classifications.extend(result)

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
                    dedup_key=_deadline_dedup_key(deadline),
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
