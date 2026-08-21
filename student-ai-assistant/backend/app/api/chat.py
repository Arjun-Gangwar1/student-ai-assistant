"""
Chat / Q&A API.

`student_id` used to arrive in the request body, which meant any caller could
ask questions answered from another student's retrieved context — including the
contents of their Gmail. Identity now comes from the session alone.
"""

import logging
from typing import Literal, Optional

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field

from app.api.deps import CurrentStudent
from app.config import settings
from app.rag.generator import answer_question
from app.utils.ratelimit import RateLimiter

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/chat", tags=["chat"])

# Each Q&A is one embedding plus one Groq completion. The cap protects the
# shared free-tier quota from a single runaway client.
_chat_limiter = RateLimiter(
    max_calls=settings.chat_rate_limit_per_day,
    window_seconds=24 * 3600,
    name="chat",
)


class ChatMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(max_length=4000)


class ChatRequest(BaseModel):
    question: str = Field(min_length=1, max_length=1000)
    # Trimmed server-side regardless of what the client sends: history is echoed
    # straight into the prompt, so its length is a cost and a prompt-injection
    # surface, not something to take on trust.
    history: Optional[list[ChatMessage]] = Field(default=None, max_length=20)


class Source(BaseModel):
    id: str
    title: str
    source: str
    created_at: Optional[str] = None
    deadline: Optional[str] = None


class ChatResponse(BaseModel):
    answer: str
    sources: list[Source]
    remaining_today: int


@router.post("/ask", response_model=ChatResponse)
async def ask(body: ChatRequest, student: CurrentStudent):
    question = body.question.strip()
    if not question:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Question cannot be empty")

    student_id = str(student["id"])
    allowed, remaining, retry_after = _chat_limiter.check(student_id)
    if not allowed:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"Daily question limit reached ({settings.chat_rate_limit_per_day}). Resets in {retry_after // 3600 + 1}h.",
            headers={"Retry-After": str(retry_after)},
        )

    history = [m.model_dump() for m in (body.history or [])][-8:]

    result = await answer_question(
        question=question,
        student_ids=[student_id],
        chat_history=history,
        year=student.get("year"),
        branch=student.get("branch"),
    )

    return ChatResponse(
        answer=result["answer"],
        sources=[Source(**s) for s in result["sources"]],
        remaining_today=remaining,
    )


@router.get("/quota")
async def quota(student: CurrentStudent):
    return {
        "remaining_today": _chat_limiter.peek(str(student["id"])),
        "daily_limit": settings.chat_rate_limit_per_day,
    }
