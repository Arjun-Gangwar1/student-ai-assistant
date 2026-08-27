"""
Classifies an item into category, priority and relevance.

Runs on every ingested item, so it is the pipeline's dominant LLM cost. Keep the
prompt short and the input capped.
"""

import logging

from app.intelligence.llm_client import llm, parse_json_response
from app.utils.date_utils import now_ist

logger = logging.getLogger(__name__)

CATEGORIES = {
    "academic", "admin", "event", "transport",
    "mess", "placement", "hostel", "general",
}
PRIORITIES = {"HIGH", "MEDIUM", "LOW"}

CLASSIFY_SYSTEM = (
    "You classify communications received by students at IIT Dharwad, an Indian "
    "engineering institute. You return only valid JSON, with no commentary."
)

CLASSIFY_PROMPT = """Classify this student communication.

Student: year {year}, branch {branch}
Today: {today} (IST)

category — exactly one of:
  academic   assignments, exams, quizzes, lectures, syllabus, grades
  admin      fees, registration, official circulars, documents
  event      workshops, hackathons, fests, seminars, sports
  transport  bus and shuttle schedules
  mess       mess menu, food, canteen
  placement  internships, jobs, PPOs, campus recruitment
  hostel     hostel notices, warden, rooms, maintenance
  general    anything else, including newsletters and automated mail

priority — exactly one of:
  HIGH    needs action within 48h, or is highly specific to this student
  MEDIUM  needs action within a week, or is moderately relevant
  LOW     informational, or not relevant to this student's year/branch

relevance — 0.0 to 1.0, how much this matters to THIS student specifically.
  A notice for a different year or branch scores below 0.3 regardless of urgency.

one_line_summary — under 100 characters, stating the concrete action or fact.
  Good: "Assignment 3 (Linear Algebra) due Friday 6pm on Classroom"
  Bad:  "This email is about an assignment"

Text:
{text}

Return only:
{{"category":"academic","priority":"HIGH","relevance":0.9,"one_line_summary":"..."}}"""

# Applied when the LLM is unreachable or returns nonsense. Deliberately low
# priority and mid relevance: an unclassified item should never jump the queue
# in a digest on the strength of a failed call.
FALLBACK = {
    "category": "general",
    "priority": "LOW",
    "relevance_score": 0.4,
    "summary": "",
    # Set only when the model did not actually answer. A genuine "general"
    # verdict and a quota-exhausted failure produced byte-identical rows, so a
    # day of 429s silently marked 93% of a corpus classified and, because
    # processed_at was stamped anyway, nothing ever retried them. Callers must
    # branch on this rather than on the values.
    "degraded": True,
}


async def classify_item(
    raw_content: str,
    title: str | None = None,
    year: int | None = None,
    branch: str | None = None,
) -> dict:
    """Returns {category, priority, relevance_score, summary}, never raises."""
    text = f"{title}\n\n{raw_content}" if title else raw_content
    text = text.strip()
    if not text:
        # Nothing to classify is a real answer, not a degraded one; retrying an
        # empty item forever would burn quota to reach the same conclusion.
        return dict(FALLBACK, degraded=False)

    prompt = CLASSIFY_PROMPT.format(
        year=year or "unknown",
        branch=branch or "unknown",
        today=now_ist().strftime("%A, %d %B %Y"),
        text=text[:1500],
    )

    try:
        raw = await llm().chat(
            messages=[
                {"role": "system", "content": CLASSIFY_SYSTEM},
                {"role": "user", "content": prompt},
            ],
            json_mode=True,
            temperature=0.0,
            max_tokens=200,
        )
    except Exception as exc:
        logger.error("Classification call failed: %s", exc)
        return dict(FALLBACK)

    result = parse_json_response(raw)
    if not result:
        return dict(FALLBACK)
    return _normalise(result)


def _normalise(result: dict) -> dict:
    """
    Coerce one model result into the shape the DB accepts.

    Validate rather than trust: an out-of-vocabulary category would violate the
    CHECK constraint and fail the write for the whole item.
    """
    category = str(result.get("category", "")).lower().strip()
    if category not in CATEGORIES:
        logger.debug("Unknown category %r from model, using 'general'", category)
        category = "general"

    priority = str(result.get("priority", "")).upper().strip()
    if priority not in PRIORITIES:
        priority = "LOW"

    try:
        relevance = float(result.get("relevance", 0.5))
    except (TypeError, ValueError):
        relevance = 0.5
    relevance = min(1.0, max(0.0, relevance))

    summary = str(result.get("one_line_summary", "") or "").strip()[:300]

    return {
        "category": category,
        "priority": priority,
        "relevance_score": relevance,
        "summary": summary,
        "degraded": False,
    }


# ── Batched classification ───────────────────────────────────────────────────
#
# Groq's free tier caps requests per DAY, not just tokens, and classification is
# one request per item with no exceptions. A 350-item first sync burned 40% of
# the 1000/day allowance for a single student, so roughly five signups would
# exhaust the quota and every later student would silently receive nothing but
# FALLBACK. Batching is the only lever that changes the shape of that cost:
# ten items per call turns ~350 requests into ~35.

BATCH_SIZE = 10

# Far tighter than the 1500 a solo classify allows: ten of those would be a
# 15k-character prompt, and the per-minute token ceiling is 8000. The corpus
# median item is ~200 characters and the 90th percentile ~1400, so this keeps
# most items whole while bounding the worst case.
BATCH_ITEM_CHARS = 700

BATCH_PROMPT = """Classify each student communication below.

Student: year {year}, branch {branch}
Today: {today} (IST)

{rules}

Items:
{items}

Return only JSON, one object per item, echoing each item's id:
{{"results":[{{"id":1,"category":"academic","priority":"HIGH","relevance":0.9,"one_line_summary":"..."}}]}}
Return exactly {count} objects, one for every id from 1 to {count}."""


def _batch_rules() -> str:
    """The category/priority definitions, shared with the single-item prompt."""
    body = CLASSIFY_PROMPT.split("category — exactly one of:", 1)[1]
    return "category — exactly one of:" + body.split("Text:", 1)[0].rstrip()


async def classify_items(
    items: list[dict],
    year: int | None = None,
    branch: str | None = None,
) -> list[dict] | None:
    """
    Classify several items in one request.

    Returns results positionally aligned with `items`, or None if the response
    could not be trusted -- a partial or misaligned batch would attach one
    student's summary to another item, so the caller retries individually
    rather than persisting a guess.
    """
    if not items:
        return []

    lines = []
    for index, item in enumerate(items, start=1):
        title = (item.get("title") or "").strip()
        body = (item.get("raw_content") or "").strip()[:BATCH_ITEM_CHARS]
        lines.append(f"[{index}] {title}\n{body}".strip())

    prompt = BATCH_PROMPT.format(
        year=year or "unknown",
        branch=branch or "unknown",
        today=now_ist().strftime("%A, %d %B %Y"),
        rules=_batch_rules(),
        items="\n\n".join(lines),
        count=len(items),
    )

    try:
        raw = await llm().chat(
            messages=[
                {"role": "system", "content": CLASSIFY_SYSTEM},
                {"role": "user", "content": prompt},
            ],
            json_mode=True,
            temperature=0.0,
            max_tokens=120 * len(items) + 100,
        )
    except Exception as exc:
        logger.error("Batch classification call failed (%d items): %s", len(items), exc)
        return None

    payload = parse_json_response(raw)
    results = payload.get("results")
    if not isinstance(results, list):
        logger.warning("Batch classification returned no results array")
        return None

    # Map by echoed id rather than by position: a model that drops or reorders
    # an entry would otherwise shift every subsequent item's classification
    # onto the wrong row, which is far worse than not classifying at all.
    by_id: dict[int, dict] = {}
    for entry in results:
        if not isinstance(entry, dict):
            continue
        try:
            by_id[int(entry.get("id"))] = entry
        except (TypeError, ValueError):
            continue

    missing = [i for i in range(1, len(items) + 1) if i not in by_id]
    if missing:
        logger.warning(
            "Batch classification missing %d/%d ids — falling back to per-item",
            len(missing), len(items),
        )
        return None

    return [_normalise(by_id[i]) for i in range(1, len(items) + 1)]
