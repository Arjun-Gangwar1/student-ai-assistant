"""
Extracts deadlines and action items from raw text using Groq JSON mode.
TRUST RULE: never auto-confirm deadlines with confidence < 0.8
"""

import logging
from datetime import datetime

from app.intelligence.llm_client import llm, parse_json_response

logger = logging.getLogger(__name__)

EXTRACT_SYSTEM = """You are an academic deadline extractor for Indian college students.
Extract every deadline, due date, event date, and registration deadline from the text.
Be precise with dates. Return ONLY valid JSON."""

EXTRACT_PROMPT = """Extract all deadlines and important dates from this text.
Today's date: {today} (current academic year context)

Text:
{text}

Return ONLY this JSON:
{{
  "deadlines": [
    {{
      "title": "Assignment 3 submission",
      "due_at": "2026-02-18T23:59:00+05:30",
      "confidence": 0.95,
      "action_required": "submit on Google Classroom"
    }}
  ]
}}

Rules:
- Use ISO 8601 with timezone (+05:30 for IST)
- confidence: 1.0 = explicit date stated, 0.7 = inferred, 0.5 = very vague
- If no deadlines found return {{"deadlines": []}}
- Never invent dates not present in the text"""


async def extract_deadlines(raw_content: str) -> list[dict]:
    """
    Returns list of dicts: title, due_at (ISO str), confidence, action_required.
    Filters out low-confidence extractions.
    """
    prompt = EXTRACT_PROMPT.format(
        today=datetime.now().strftime("%Y-%m-%d"),
        text=raw_content[:2000],
    )

    try:
        raw = llm().chat(
            messages=[
                {"role": "system", "content": EXTRACT_SYSTEM},
                {"role": "user", "content": prompt},
            ],
            json_mode=True,
            temperature=0.0,
        )
        result = parse_json_response(raw)
        deadlines = result.get("deadlines", [])

        validated = []
        for d in deadlines:
            if not d.get("title") or not d.get("due_at"):
                continue
            confidence = float(d.get("confidence", 0.5))
            validated.append({
                "title": d["title"],
                "due_at": d["due_at"],
                "confidence": confidence,
                "action_required": d.get("action_required", ""),
                # confirmed=True only if confidence >= 0.8 (TRUST RULE)
                "confirmed": confidence >= 0.8,
            })

        return validated

    except Exception as e:
        logger.error(f"Deadline extraction failed: {e}")
        return []
