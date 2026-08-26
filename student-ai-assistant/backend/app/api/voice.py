"""
Voice API — speech in, speech out.

A separate router from chat.py: STT/TTS are a different Groq product from
the chat model (billed and rate-limited independently by Groq), so they get
their own daily allowance rather than sharing chat's question quota — a
student composing a question by voice, then asking to hear the answer back,
should not burn the same budget as asking twice.
"""

import logging

from fastapi import APIRouter, File, HTTPException, UploadFile, status
from fastapi.responses import Response
from pydantic import BaseModel, Field

from app.api.deps import CurrentStudent
from app.config import settings
from app.intelligence.llm_client import LLMError
from app.intelligence.voice import synthesize_speech, transcribe_audio
from app.utils.ratelimit import RateLimiter

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/voice", tags=["voice"])

# Groq free tier: 25MB per transcription request.
MAX_AUDIO_BYTES = 25 * 1024 * 1024
AUDIO_EXTENSIONS = (".flac", ".mp3", ".mp4", ".mpeg", ".mpga", ".m4a", ".ogg", ".wav", ".webm")

_stt_limiter = RateLimiter(
    max_calls=settings.voice_rate_limit_per_day, window_seconds=24 * 3600, name="stt",
)
_tts_limiter = RateLimiter(
    max_calls=settings.voice_rate_limit_per_day, window_seconds=24 * 3600, name="tts",
)


def _check(limiter: RateLimiter, student_id: str) -> None:
    allowed, _remaining, retry_after = limiter.check(student_id)
    if not allowed:
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            f"Daily voice limit reached ({settings.voice_rate_limit_per_day}). "
            f"Resets in {retry_after // 3600 + 1}h.",
            headers={"Retry-After": str(retry_after)},
        )


class SpeakRequest(BaseModel):
    text: str = Field(min_length=1, max_length=4000)


@router.post("/transcribe")
async def transcribe(student: CurrentStudent, file: UploadFile = File(...)):
    """Speech to text — used to fill the chat composer from a recorded clip."""
    student_id = str(student["id"])
    _check(_stt_limiter, student_id)

    filename = file.filename or "audio.webm"
    if not filename.lower().endswith(AUDIO_EXTENSIONS):
        raise HTTPException(
            status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            f"Unsupported audio type. Supported: {', '.join(AUDIO_EXTENSIONS)}",
        )

    data = await file.read()
    if len(data) > MAX_AUDIO_BYTES:
        raise HTTPException(
            status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            f"Recording too large — max {MAX_AUDIO_BYTES // (1024 * 1024)}MB.",
        )
    if not data:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Empty recording.")

    try:
        text = await transcribe_audio(data, filename)
    except LLMError as exc:
        logger.error("Transcription failed: %s", exc)
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY, "Couldn't transcribe that. Please try again."
        ) from exc

    if not text:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY, "Didn't catch any speech in that recording."
        )
    return {"text": text}


@router.post("/speak")
async def speak(body: SpeakRequest, student: CurrentStudent):
    """Text to speech — reads an assistant answer aloud. Returns a WAV clip."""
    _check(_tts_limiter, str(student["id"]))

    try:
        audio = await synthesize_speech(body.text)
    except LLMError as exc:
        logger.error("Speech synthesis failed: %s", exc)
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY, "Couldn't generate audio right now."
        ) from exc

    return Response(content=audio, media_type="audio/wav")
