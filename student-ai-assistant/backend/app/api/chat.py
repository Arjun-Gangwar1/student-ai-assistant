"""
Chat API — streaming answers and persistent conversations.

`student_id` used to arrive in the request body, which meant any caller could ask
questions answered from another student's retrieved context, including their
Gmail. Identity now comes from the session alone.
"""

import json
import logging
from typing import AsyncIterator, Literal, Optional

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app.api.deps import CurrentStudent, CurrentStudentId
from app.config import settings
from app.connectors.gmail_conn import TEXT_EXTENSIONS, extract_text_from_bytes
from app.db import queries
from app.intelligence.llm_client import llm
from app.intelligence.uploads import MAX_UPLOAD_BYTES, UPLOAD_TEXT_CHARS, index_upload
from app.rag.generator import answer_about_document, answer_question, stream_answer
from app.utils.ratelimit import RateLimiter

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/chat", tags=["chat"])

_chat_limiter = RateLimiter(
    max_calls=settings.chat_rate_limit_per_day,
    window_seconds=24 * 3600,
    name="chat",
)


# ─── Schemas ─────────────────────────────────────────────────────────────────

class ChatMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(max_length=8000)


class AskRequest(BaseModel):
    question: str = Field(min_length=1, max_length=2000)
    conversation_id: Optional[str] = None
    # Trimmed server-side regardless of what a client sends: history is echoed
    # into the prompt, so its length is both a cost and an injection surface.
    history: Optional[list[ChatMessage]] = Field(default=None, max_length=20)
    # Replace the previous answer to this same question rather than asking it
    # again — requires an existing conversation_id.
    regenerate: bool = False


class Source(BaseModel):
    id: str
    title: str
    source: str
    created_at: Optional[str] = None
    deadline: Optional[str] = None


class ChatResponse(BaseModel):
    answer: str
    sources: list[Source]
    conversation_id: Optional[str] = None
    remaining_today: int


class ConversationOut(BaseModel):
    id: str
    title: str
    updated_at: str
    message_count: int = 0
    preview: Optional[str] = None


class RenameRequest(BaseModel):
    title: str = Field(min_length=1, max_length=200)


# ─── Helpers ─────────────────────────────────────────────────────────────────

def _sse(event: str, data) -> str:
    """One Server-Sent Event frame."""
    return f"event: {event}\ndata: {json.dumps(data, default=str)}\n\n"


def _check_quota(student_id: str) -> int:
    allowed, remaining, retry_after = _chat_limiter.check(student_id)
    if not allowed:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"Daily question limit reached ({settings.chat_rate_limit_per_day}). "
                   f"Resets in {retry_after // 3600 + 1}h.",
            headers={"Retry-After": str(retry_after)},
        )
    return remaining


async def _resolve_conversation(
    conversation_id: str | None, student_id: str, question: str
) -> str:
    """Return a conversation id the student owns, creating one if needed."""
    if conversation_id:
        if await queries.get_conversation(conversation_id, student_id) is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Conversation not found")
        return conversation_id
    created = await queries.create_conversation(student_id, first_message=question)
    return str(created["id"])


# ─── Streaming ───────────────────────────────────────────────────────────────

@router.post("/stream")
async def ask_streaming(body: AskRequest, request: Request, student: CurrentStudent):
    """
    Stream an answer as Server-Sent Events.

    Events: `sources` once, then `delta` per token chunk, then `done`.

    Streaming does not speed up generation — it moves first output from ~1.3s to
    ~500ms, which is most of the difference between a chat that feels instant and
    one that feels stuck.
    """
    question = body.question.strip()
    if not question:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Question cannot be empty")

    student_id = str(student["id"])
    remaining = _check_quota(student_id)

    if body.regenerate:
        if not body.conversation_id:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST, "regenerate requires an existing conversation"
            )
        conversation_id = await _resolve_conversation(body.conversation_id, student_id, question)
        # The old reply is removed rather than left in place: it did not answer
        # well enough to keep, and a second row for the same question would
        # otherwise sit alongside the new one in history.
        await queries.delete_last_assistant_message(conversation_id, student_id)
    else:
        conversation_id = await _resolve_conversation(body.conversation_id, student_id, question)
        await queries.add_message(conversation_id, "user", question)

    # Prefer stored history over whatever the client sent: it is authoritative,
    # survives a page reload, and cannot be used to smuggle fabricated turns into
    # the prompt.
    history = await queries.recent_turns(conversation_id, limit=9)
    history = [h for h in history if h["content"] != question][-8:]

    async def event_stream() -> AsyncIterator[str]:
        placeholder = await queries.add_message(
            conversation_id, "assistant", "", model=llm().model, completed=False
        )
        message_id = str(placeholder["id"])
        sources: list = []
        final = ""

        yield _sse("start", {"conversation_id": conversation_id, "message_id": message_id})

        try:
            async for kind, payload in stream_answer(
                question=question,
                student_ids=[student_id],
                chat_history=history,
                year=student.get("year"),
                branch=student.get("branch"),
                student=student,
            ):
                # Client navigated away or hit stop — stop generating rather than
                # burning tokens on an answer nobody will read.
                if await request.is_disconnected():
                    logger.info("Client disconnected; aborting stream %s", message_id)
                    break

                if kind == "sources":
                    sources = payload
                    yield _sse("sources", payload)
                elif kind == "delta":
                    yield _sse("delta", {"text": payload})
                elif kind == "error":
                    yield _sse("error", {"detail": payload})
                    await queries.finish_message(message_id, final, sources, error=str(payload))
                    return
                elif kind == "done":
                    final = payload
        except Exception as exc:
            logger.exception("Streaming failed: %s", exc)
            yield _sse("error", {"detail": "Generation failed. Please try again."})
            await queries.finish_message(message_id, final, sources, error=str(exc))
            return

        await queries.finish_message(message_id, final, sources)
        yield _sse("done", {
            "message_id": message_id,
            "conversation_id": conversation_id,
            "remaining_today": remaining,
        })

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            # nginx and similar proxies buffer responses by default, which would
            # hold every chunk until the end and undo the entire point.
            "X-Accel-Buffering": "no",
        },
    )


# ─── Non-streaming (kept for Telegram and simple clients) ───────────────────

@router.post("/ask", response_model=ChatResponse)
async def ask(body: AskRequest, student: CurrentStudent):
    question = body.question.strip()
    if not question:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Question cannot be empty")

    student_id = str(student["id"])
    remaining = _check_quota(student_id)

    conversation_id = None
    history: list[dict] = []
    if body.conversation_id:
        conversation_id = await _resolve_conversation(body.conversation_id, student_id, question)
        await queries.add_message(conversation_id, "user", question)
        history = await queries.recent_turns(conversation_id, limit=9)
        history = [h for h in history if h["content"] != question][-8:]
    elif body.history:
        history = [m.model_dump() for m in body.history][-8:]

    result = await answer_question(
        question=question,
        student_ids=[student_id],
        chat_history=history,
        year=student.get("year"),
        branch=student.get("branch"),
        student=student,
    )

    if conversation_id:
        await queries.add_message(
            conversation_id, "assistant", result["answer"],
            sources=result["sources"], model=llm().model,
        )

    return ChatResponse(
        answer=result["answer"],
        sources=[Source(**s) for s in result["sources"]],
        conversation_id=conversation_id,
        remaining_today=remaining,
    )


# ─── Document upload ─────────────────────────────────────────────────────────

@router.post("/upload", response_model=ChatResponse)
async def upload_document(
    student: CurrentStudent,
    file: UploadFile = File(...),
    question: str = Form(""),
    conversation_id: Optional[str] = Form(None),
):
    """
    Upload a document, get an answer now, and keep it searchable afterward.

    Both halves happen in this one request: the extracted text goes straight
    into the prompt for an immediate answer (see answer_about_document — it
    does not wait on retrieval ranking a same-request upload highly), and
    separately becomes a normal `items` row with an embedding, so a later
    question in another conversation can still find it via hybrid search.
    """
    filename = file.filename or "upload"
    if not filename.lower().endswith(TEXT_EXTENSIONS):
        raise HTTPException(
            status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            f"Unsupported file type. Supported: {', '.join(TEXT_EXTENSIONS)}",
        )

    data = await file.read()
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(
            status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            f"File too large — max {MAX_UPLOAD_BYTES // (1024 * 1024)}MB.",
        )

    text = extract_text_from_bytes(
        data, file.content_type or "", filename, max_chars=UPLOAD_TEXT_CHARS
    )
    if not text.strip():
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "Couldn't read any text from this file. It may be a scanned image "
            "or an unsupported format.",
        )

    student_id = str(student["id"])
    remaining = _check_quota(student_id)

    question = question.strip() or f"Summarise {filename} and flag any deadline it mentions."
    user_note = f"📎 {filename}\n{question}"

    conversation_id = await _resolve_conversation(conversation_id, student_id, user_note)
    await queries.add_message(conversation_id, "user", user_note)

    history = await queries.recent_turns(conversation_id, limit=9)
    history = [h for h in history if h["content"] != user_note][-8:]

    item = await index_upload(
        student_id=student_id, filename=filename, text=text,
        year=student.get("year"), branch=student.get("branch"),
    )
    document_source = {
        "id": str(item["id"]), "title": filename, "source": "upload",
        "created_at": item["created_at"].isoformat() if item.get("created_at") else None,
        "deadline": None,
    }

    result = await answer_about_document(
        question=question, filename=filename, document_text=text,
        document_source=document_source, student_ids=[student_id],
        chat_history=history, year=student.get("year"), branch=student.get("branch"),
        student=student,
    )

    await queries.add_message(
        conversation_id, "assistant", result["answer"],
        sources=result["sources"], model=llm().model,
    )

    return ChatResponse(
        answer=result["answer"],
        sources=[Source(**s) for s in result["sources"]],
        conversation_id=conversation_id,
        remaining_today=remaining,
    )


# ─── Conversations ───────────────────────────────────────────────────────────

@router.get("/conversations")
async def list_conversations(student_id: CurrentStudentId):
    rows = await queries.list_conversations(student_id)
    return {
        "conversations": [
            {
                "id": str(r["id"]),
                "title": r["title"],
                "updated_at": r["updated_at"].isoformat(),
                "message_count": r["message_count"],
                "preview": r["preview"],
            }
            for r in rows
        ]
    }


@router.post("/conversations", status_code=status.HTTP_201_CREATED)
async def new_conversation(student_id: CurrentStudentId):
    created = await queries.create_conversation(student_id)
    return {
        "id": str(created["id"]),
        "title": created["title"],
        "updated_at": created["updated_at"].isoformat(),
    }


@router.get("/conversations/{conversation_id}")
async def get_conversation(conversation_id: str, student_id: CurrentStudentId):
    conversation = await queries.get_conversation(conversation_id, student_id)
    if conversation is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Conversation not found")

    messages = await queries.get_messages(conversation_id, student_id)
    return {
        "id": str(conversation["id"]),
        "title": conversation["title"],
        "messages": [
            {
                "id": str(m["id"]),
                "role": m["role"],
                "content": m["content"],
                "sources": m["sources"],
                "created_at": m["created_at"].isoformat(),
                "error": m["error"],
            }
            # Drop empty assistant rows: an aborted stream leaves a placeholder
            # that would otherwise render as a blank bubble on reload.
            for m in messages
            if m["content"] or m["role"] == "user"
        ],
    }


@router.patch("/conversations/{conversation_id}")
async def rename_conversation(
    conversation_id: str, body: RenameRequest, student_id: CurrentStudentId
):
    updated = await queries.rename_conversation(conversation_id, student_id, body.title)
    if updated is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Conversation not found")
    return {"id": str(updated["id"]), "title": updated["title"]}


@router.delete("/conversations/{conversation_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_conversation(conversation_id: str, student_id: CurrentStudentId):
    if not await queries.delete_conversation(conversation_id, student_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Conversation not found")


@router.get("/quota")
async def quota(student: CurrentStudent):
    return {
        "remaining_today": _chat_limiter.peek(str(student["id"])),
        "daily_limit": settings.chat_rate_limit_per_day,
    }
