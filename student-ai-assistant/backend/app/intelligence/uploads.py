"""
Ad-hoc document indexing for chat uploads.

A document dragged into chat gets the same treatment as anything the sync
pipeline brings in — an items row, a local embedding, and (budget permitting)
an LLM classification — so a later question in a different conversation can
still find it via the same hybrid search everything else goes through.

Classification is skipped (falling back to the same FALLBACK the sync
pipeline uses for any failed call) when the daily LLM budget is exhausted —
the caller already gets an answer generated directly from the document text
(see app.rag.generator.answer_about_document), so a generic category costs
nothing the student can feel right now. The embedding is saved regardless:
it is a local model with no quota, so there is no budget reason to skip it.
"""

import logging
from uuid import uuid4

from app.db import queries
from app.intelligence.classifier import FALLBACK, classify_item
from app.intelligence.embedder import embed_text
from app.utils import token_budget

logger = logging.getLogger(__name__)

MAX_UPLOAD_BYTES = 8 * 1024 * 1024
UPLOAD_TEXT_CHARS = 10_000


async def index_upload(
    student_id: str,
    filename: str,
    text: str,
    year: int | None = None,
    branch: str | None = None,
) -> dict:
    """Persist an uploaded document as a searchable item. Returns the item row."""
    item = await queries.upsert_item(
        student_id=student_id,
        source="upload",
        source_id=str(uuid4()),
        raw_content=text,
        title=filename,
        metadata={"filename": filename, "char_count": len(text)},
    )

    try:
        vector = await embed_text(f"{filename}\n{text[:2000]}")
    except Exception as exc:
        logger.error("Embedding upload %r failed: %s", filename, exc)
        vector = None

    classification = (
        await classify_item(raw_content=text, title=filename, year=year, branch=branch)
        if token_budget.allow_chat()
        else dict(FALLBACK)
    )

    await queries.save_item_analysis(
        item_id=str(item["id"]),
        category=classification["category"],
        priority=classification["priority"],
        relevance_score=classification["relevance_score"],
        summary=classification["summary"] or filename,
        embedding=vector,
        # A placeholder must not look classified, or the background pipeline
        # will skip it forever and the document keeps a filename for a summary.
        mark_processed=not classification.get("degraded"),
    )

    return item
