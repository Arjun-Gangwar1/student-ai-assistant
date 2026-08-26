"""
Voice: Groq Whisper for speech-to-text, Groq Orpheus for text-to-speech.

Separate from llm_client.py's chat abstraction — these are a different Groq
API surface (audio, not chat completions). Speech-to-text goes through the
pinned SDK's `audio.transcriptions`, which it does support. Text-to-speech
does not: `audio.speech` was added to the groq SDK after the pinned 0.11.0
(see llm_client.py's own note on why that version is pinned), so TTS calls
the same OpenAI-compatible REST endpoint directly with httpx instead of
bumping a dependency pinned deliberately elsewhere.

Verified live against the real API (2026-08-26), not assumed:
  - whisper-large-v3-turbo transcribes with no special gating.
  - The /audio/speech endpoint caps `input` at 200 characters per call
    (stated explicitly in Groq's docs, under Orpheus' Limitations) — a
    normal chat answer needs several calls, stitched into one clip.
  - canopylabs/orpheus-v1-english returns `model_terms_required` until the
    org admin accepts the model's terms in the Groq console — a one-time
    account-level step this code cannot do on its own. Until that happens
    every /speak call fails upstream; the error message says so in the
    server log even though the API response to the student stays a plain
    502, matching how every other upstream failure here is surfaced.
"""

import io
import logging
import re
import wave

import httpx
from groq import AsyncGroq

from app.config import settings
from app.intelligence.llm_client import LLMError

logger = logging.getLogger(__name__)

TTS_CHUNK_CHARS = 200
# Six chunks (~1200 chars) is a generous full chat answer read aloud; beyond
# that this is closer to reading a document than hearing a reply.
_TTS_MAX_CHUNKS = 6
_TTS_TERMS_URL = "https://console.groq.com/playground?model=canopylabs%2Forpheus-v1-english"

_client: AsyncGroq | None = None


def _groq_client() -> AsyncGroq:
    global _client
    if _client is None:
        if not settings.groq_api_key:
            raise LLMError("GROQ_API_KEY is not set")
        _client = AsyncGroq(api_key=settings.groq_api_key, timeout=30.0)
    return _client


async def transcribe_audio(data: bytes, filename: str) -> str:
    """Speech to text. Returns '' if nothing intelligible was heard."""
    try:
        resp = await _groq_client().audio.transcriptions.create(
            model=settings.groq_stt_model,
            file=(filename, data),
        )
    except Exception as exc:
        raise LLMError(f"groq stt: {type(exc).__name__}: {exc}") from exc
    return (resp.text or "").strip()


_MD_LINK = re.compile(r"\[([^\]]+)\]\([^)]+\)")
_MD_STRIP = re.compile(r"[*_`#]+")
_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+")


def _strip_markdown(text: str) -> str:
    """Spoken text shouldn't include **, `, #, or raw link syntax."""
    text = _MD_LINK.sub(r"\1", text)
    text = _MD_STRIP.sub("", text)
    return re.sub(r"\s+", " ", text).strip()


def _tts_chunks(text: str, limit: int = TTS_CHUNK_CHARS) -> list[str]:
    """
    Split into pieces that fit the API's per-request character cap, breaking
    on sentence boundaries where possible so a chunk never cuts off mid-word.
    """
    text = text.strip()
    if not text:
        return []

    sentences = [s for s in _SENTENCE_SPLIT.split(text) if s.strip()]
    chunks: list[str] = []
    current = ""

    for sentence in sentences:
        candidate = f"{current} {sentence}".strip() if current else sentence
        if len(candidate) <= limit:
            current = candidate
            continue
        if current:
            chunks.append(current)
        # A single sentence longer than the limit still has to be split, even
        # mid-sentence — better than dropping it or exceeding the API's cap.
        current = sentence
        while len(current) > limit:
            chunks.append(current[:limit])
            current = current[limit:]

    if current:
        chunks.append(current)
    return chunks[:_TTS_MAX_CHUNKS]


def _stitch_wav(clips: list[bytes]) -> bytes:
    """Concatenate sequential WAV clips (same format) into one playable file."""
    params = None
    frames: list[bytes] = []
    for clip in clips:
        with wave.open(io.BytesIO(clip), "rb") as reader:
            if params is None:
                params = reader.getparams()
            frames.append(reader.readframes(reader.getnframes()))

    out = io.BytesIO()
    with wave.open(out, "wb") as writer:
        writer.setparams(params)
        for chunk in frames:
            writer.writeframes(chunk)
    return out.getvalue()


async def _speak_chunk(client: httpx.AsyncClient, text: str) -> bytes:
    resp = await client.post(
        "https://api.groq.com/openai/v1/audio/speech",
        headers={"Authorization": f"Bearer {settings.groq_api_key}"},
        json={"model": settings.groq_tts_model, "input": text, "voice": settings.groq_tts_voice},
    )
    if resp.status_code == 200:
        return resp.content

    try:
        error = resp.json().get("error", {})
    except ValueError:
        error = {}
    message = error.get("message") or f"TTS request failed ({resp.status_code})"
    if error.get("code") == "model_terms_required":
        message = (
            f"{message} — accept the model's terms as the org admin at "
            f"{_TTS_TERMS_URL}, then try again."
        )
    raise LLMError(f"groq tts: {message}")


async def synthesize_speech(text: str) -> bytes:
    """
    Text to speech, as one stitched WAV clip.

    Sequential per-chunk calls, not concurrent: Orpheus's own rate limit is
    unknown and untested against real traffic, and correctness (clip order)
    matters more here than shaving a few hundred ms off a handful of calls.
    """
    if not settings.groq_api_key:
        raise LLMError("GROQ_API_KEY is not set")

    chunks = _tts_chunks(_strip_markdown(text))
    if not chunks:
        raise LLMError("Nothing to say")

    async with httpx.AsyncClient(timeout=30.0) as client:
        clips = [await _speak_chunk(client, chunk) for chunk in chunks]

    return _stitch_wav(clips)
