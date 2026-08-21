"""
Classifies raw item text into category, priority, relevance score.
Uses Groq Llama-3.1-8B with JSON mode for structured output.
"""

import logging
from datetime import datetime

from app.intelligence.llm_client import llm, parse_json_response

logger = logging.getLogger(__name__)

CLASSIFY_SYSTEM = """You are a classifier for Indian college student communications at IIT Dharwad.
Classify each item accurately. Return ONLY valid JSON — no explanation."""

CLASSIFY_PROMPT = """Classify this student communication.

Student profile: Year {year}, Branch {branch}
Today's date: {today}

Categories (pick ONE):
- academic   : assignments, exams, lectures, syllabus, grades
- admin      : fees, registration, official notices, circulars
- event      : workshops, hackathons, seminars, cultural, sports
- transport  : bus, shuttle, transport schedule changes
- mess       : mess menu, food, canteen
- placement  : internships, jobs, PPO, campus recruitment
- hostel     : hostel notices, warden, room, maintenance
- general    : everything else

Priority (pick ONE based on urgency + relevance):
- HIGH   : deadline within 48h OR extremely relevant to student
- MEDIUM : deadline within 7 days OR moderately relevant
- LOW    : low urgency or low relevance

Text to classify:
{text}

Return ONLY this JSON:
{{
  "category": "academic",
  "priority": "HIGH",
  "relevance": 0.9,
  "one_line_summary": "Assignment 3 due in 2 days for Linear Algebra"
}}"""


async def classify_item(
    raw_content: str,
    year: int | None = None,
    branch: str | None = None,
) -> dict:
    """
    Returns dict with keys: category, priority, relevance, one_line_summary.
    Defaults to safe values on failure.
    """
    prompt = CLASSIFY_PROMPT.format(
        year=year or "unknown",
        branch=branch or "unknown",
        today=datetime.now().strftime("%Y-%m-%d"),
        text=raw_content[:1500],  # cap to avoid token overflow
    )

    try:
        raw = llm().chat(
            messages=[
                {"role": "system", "content": CLASSIFY_SYSTEM},
                {"role": "user", "content": prompt},
            ],
            json_mode=True,
            temperature=0.0,
        )
        result = parse_json_response(raw)

        return {
            "category": result.get("category", "general"),
            "priority": result.get("priority", "LOW"),
            "relevance_score": float(result.get("relevance", 0.5)),
            "summary": result.get("one_line_summary", ""),
        }
    except Exception as e:
        logger.error(f"Classification failed: {e}")
        return {
            "category": "general",
            "priority": "LOW",
            "relevance_score": 0.3,
            "summary": "",
        }
