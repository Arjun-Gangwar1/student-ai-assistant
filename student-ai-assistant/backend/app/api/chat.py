"""
Chat / Q&A API endpoint.
Handles both REST and Telegram /ask command queries.
"""

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel
from typing import Optional

from app.rag.generator import answer_question

router = APIRouter(prefix="/api/chat", tags=["chat"])


class ChatRequest(BaseModel):
    question: str
    student_id: str
    history: Optional[list[dict]] = None


class ChatResponse(BaseModel):
    answer: str
    sources: list[dict]


@router.post("/ask", response_model=ChatResponse)
async def ask(body: ChatRequest):
    if not body.question.strip():
        raise HTTPException(status_code=400, detail="Question cannot be empty")

    result = await answer_question(
        question=body.question,
        student_id=body.student_id,
        chat_history=body.history,
    )
    return ChatResponse(**result)
