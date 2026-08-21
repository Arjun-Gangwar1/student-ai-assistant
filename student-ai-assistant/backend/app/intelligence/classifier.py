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
        return dict(FALLBACK)

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

    # Validate rather than trust: an out-of-vocabulary category would violate
    # the CHECK constraint and fail the write for the whole item.
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
    }
