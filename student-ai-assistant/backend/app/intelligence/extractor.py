"""
Deadline extraction.

Non-Negotiable Rule #1 lives here: a wrong deadline is a sev-1 bug. This module
is therefore deliberately conservative — it rejects more than it accepts, and
anything below the confidence bar is stored unconfirmed so the UI asks the
student before it is ever treated as fact.
"""

import logging
from datetime import datetime, timedelta

from app.intelligence.llm_client import llm, parse_json_response
from app.utils.date_utils import IST, ensure_aware, now_ist, now_utc, parse_iso

logger = logging.getLogger(__name__)

# Below this, a deadline is stored but never auto-confirmed and never alerted on
# until a student confirms it.
CONFIDENCE_THRESHOLD = 0.8

# A "deadline" further out than this is almost always a misparse — a year
# mistaken for a date, or an academic-calendar span read as a due date.
MAX_FUTURE_DAYS = 365
# Small backward tolerance: a notice may be processed hours after its deadline.
MAX_PAST_DAYS = 2

EXTRACT_SYSTEM = (
    "You extract deadlines from communications received by Indian college "
    "students. You are precise about dates and never invent one. "
    "You return only valid JSON."
)

EXTRACT_PROMPT = """Extract every deadline, due date, and registration cut-off from the text.

Today is {today} (IST). The current academic year runs {year_hint}.

Rules:
- Copy dates from the text. Never infer a date that is not stated.
- Resolve relative dates ("this Friday", "kal", "next Monday") against today.
- If a year is not stated, choose the interpretation that puts the date in the
  near future rather than the past.
- Times are IST. Use +05:30. If no time is given, use 23:59.
- confidence: 1.0 an explicit full date; 0.8 a clear relative date;
  0.6 a partial date needing inference; 0.4 vague ("soon", "next week").
- Ignore dates that are not deadlines: event start times already past, dates
  mentioned as history, email signature dates.
- No deadlines found → {{"deadlines": []}}

Text:
{text}

Return only:
{{"deadlines":[{{"title":"Assignment 3 submission","due_at":"2026-02-18T23:59:00+05:30","confidence":0.95,"action_required":"submit on Google Classroom"}}]}}"""


def _year_hint() -> str:
    """Indian academic year runs July–June; helps resolve bare day/month dates."""
    now = now_ist()
    start = now.year if now.month >= 7 else now.year - 1
    return f"July {start} to June {start + 1}"


def _plausible(due_at: datetime) -> tuple[bool, str]:
    """Reject dates a model produced but that cannot be a real deadline."""
    now = now_utc()
    if due_at > now + timedelta(days=MAX_FUTURE_DAYS):
        return False, f"more than {MAX_FUTURE_DAYS} days out"
    if due_at < now - timedelta(days=MAX_PAST_DAYS):
        return False, "in the past"
    return True, ""


async def extract_deadlines(raw_content: str, source: str = "unknown") -> list[dict]:
    """
    Returns a list of {title, due_at (datetime), confidence, action_required,
    confirmed}. Never raises — extraction failure must not fail ingestion.
    """
    text = (raw_content or "").strip()
    if len(text) < 20:
        return []

    prompt = EXTRACT_PROMPT.format(
        today=now_ist().strftime("%A, %d %B %Y"),
        year_hint=_year_hint(),
        text=text[:2000],
    )

    try:
        raw = await llm().chat(
            messages=[
                {"role": "system", "content": EXTRACT_SYSTEM},
                {"role": "user", "content": prompt},
            ],
            json_mode=True,
            temperature=0.0,
            max_tokens=600,
        )
    except Exception as exc:
        logger.error("Deadline extraction call failed (%s): %s", source, exc)
        return []

    parsed = parse_json_response(raw)
    candidates = parsed.get("deadlines")
    if not isinstance(candidates, list):
        return []

    validated: list[dict] = []
    for candidate in candidates:
        if not isinstance(candidate, dict):
            continue

        title = str(candidate.get("title") or "").strip()
        if not title:
            continue

        due_at = parse_iso(candidate.get("due_at"))
        if due_at is None:
            logger.debug("Discarded %r — unparseable due_at %r", title, candidate.get("due_at"))
            continue

        # A bare datetime from the model means IST, not UTC.
        due_at = ensure_aware(due_at, assume=IST)

        ok, reason = _plausible(due_at)
        if not ok:
            logger.info("Discarded deadline %r (%s): %s", title[:50], reason, due_at.isoformat())
            continue

        try:
            confidence = float(candidate.get("confidence", 0.5))
        except (TypeError, ValueError):
            confidence = 0.5
        confidence = min(1.0, max(0.0, confidence))

        validated.append({
            "title": title[:300],
            "due_at": due_at,
            "confidence": confidence,
            "action_required": str(candidate.get("action_required") or "").strip()[:300],
            # Auto-confirm only above the bar. Everything else needs a human.
            "confirmed": confidence >= CONFIDENCE_THRESHOLD,
        })

    if validated:
        logger.info("Extracted %d deadline(s) from %s", len(validated), source)
    return validated
