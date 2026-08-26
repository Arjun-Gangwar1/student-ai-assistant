"""
Voice: chunking logic (pure) plus the transcribe/speak endpoints (mocked Groq).

Uses httpx.AsyncClient over ASGITransport for the endpoint tests — TestClient
drives the app on its own event loop, which breaks the asyncpg `pool` fixture
mid-request. See test_security.py's identical note.
"""

import base64
import io
import json
import wave
from unittest.mock import AsyncMock, patch

import httpx
import itsdangerous
import pytest

from app.config import settings
from app.intelligence.voice import TTS_CHUNK_CHARS, _stitch_wav, _tts_chunks


def _session_cookie(student_id: str) -> str:
    payload = base64.b64encode(json.dumps({"student_id": student_id}).encode())
    return itsdangerous.TimestampSigner(settings.secret_key).sign(payload).decode()


def _streaming_wav_bytes(nframes: int = 100) -> bytes:
    """
    A WAV shaped the way Orpheus actually returns one.

    Because the audio is streamed, the length is unknown when the header is
    written, so the size fields carry placeholders (RIFF size 0xFFFFFFFF,
    nframes 0x7FFFFFFF) instead of real values. _wav_bytes() is too well-formed
    to catch code that trusts those fields.
    """
    raw = bytearray(_wav_bytes(nframes))
    raw[4:8] = (0xFFFFFFFF).to_bytes(4, "little")      # RIFF chunk size
    raw[40:44] = (0x7FFFFFFF * 2).to_bytes(4, "little")  # data chunk size
    return bytes(raw)


def _wav_bytes(nframes: int = 100) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes(b"\x00\x00" * nframes)
    return buf.getvalue()


class TestChunking:
    def test_short_text_is_one_chunk(self):
        assert _tts_chunks("Assignment 3 is due Friday.") == ["Assignment 3 is due Friday."]

    def test_no_chunk_exceeds_the_limit(self):
        text = " ".join(f"Sentence number {i} has some words in it." for i in range(30))
        for chunk in _tts_chunks(text):
            assert len(chunk) <= TTS_CHUNK_CHARS

    def test_sentences_are_never_split_across_chunks(self):
        """Each chunk boundary falls after a terminator, never mid-sentence."""
        text = "First sentence here. " * 20
        for chunk in _tts_chunks(text):
            assert chunk.strip().endswith(".") or len(chunk) == TTS_CHUNK_CHARS

    def test_a_single_oversized_sentence_is_hard_cut_not_dropped(self):
        sentence = "x" * 450 + "."
        chunks = _tts_chunks(sentence)
        assert sum(len(c) for c in chunks) >= 450
        assert all(len(c) <= TTS_CHUNK_CHARS for c in chunks)

    def test_empty_text_yields_no_chunks(self):
        assert _tts_chunks("   ") == []


class TestWavStitching:
    def test_stitched_wav_contains_all_frames(self):
        stitched = _stitch_wav([_wav_bytes(50), _wav_bytes(30), _wav_bytes(20)])
        with wave.open(io.BytesIO(stitched), "rb") as r:
            assert r.getnframes() == 100

    def test_streaming_placeholder_header_does_not_overflow_output(self):
        """
        Orpheus ships placeholder size fields. Propagating them into the output
        header made it declare ~4 GB of audio and blew up packing the uint32
        size on close, so every multi-chunk answer failed at the stitch.
        """
        stitched = _stitch_wav(
            [_streaming_wav_bytes(50), _streaming_wav_bytes(30), _streaming_wav_bytes(20)]
        )
        with wave.open(io.BytesIO(stitched), "rb") as r:
            assert r.getnframes() == 100
        # The real byte count, not a placeholder, must reach the header.
        assert int.from_bytes(stitched[4:8], "little") == len(stitched) - 8


@pytest.fixture
def client(pool):
    from app.main import app

    transport = httpx.ASGITransport(app=app)
    return httpx.AsyncClient(transport=transport, base_url="http://test")


@pytest.fixture
def cookies(student):
    return {"studentai_session": _session_cookie(str(student["id"]))}


@pytest.mark.db
class TestTranscribeEndpoint:
    async def test_unsupported_extension_is_rejected(self, client, cookies):
        async with client as c:
            response = await c.post(
                "/api/voice/transcribe",
                files={"file": ("clip.xyz", b"not audio", "application/octet-stream")},
                cookies=cookies,
            )
        assert response.status_code == 415

    async def test_empty_file_is_rejected(self, client, cookies):
        async with client as c:
            response = await c.post(
                "/api/voice/transcribe",
                files={"file": ("clip.webm", b"", "audio/webm")},
                cookies=cookies,
            )
        assert response.status_code == 422

    async def test_successful_transcription(self, client, cookies):
        with patch(
            "app.api.voice.transcribe_audio", AsyncMock(return_value="what is due tomorrow")
        ):
            async with client as c:
                response = await c.post(
                    "/api/voice/transcribe",
                    files={"file": ("clip.webm", b"fake-audio-bytes", "audio/webm")},
                    cookies=cookies,
                )
        assert response.status_code == 200
        assert response.json() == {"text": "what is due tomorrow"}

    async def test_silence_is_rejected_not_sent_to_chat(self, client, cookies):
        """An empty transcript (silence) must not become an empty chat question."""
        with patch("app.api.voice.transcribe_audio", AsyncMock(return_value="")):
            async with client as c:
                response = await c.post(
                    "/api/voice/transcribe",
                    files={"file": ("clip.webm", b"fake-audio-bytes", "audio/webm")},
                    cookies=cookies,
                )
        assert response.status_code == 422


@pytest.mark.db
class TestSpeakEndpoint:
    async def test_successful_synthesis_returns_wav(self, client, cookies):
        with patch(
            "app.api.voice.synthesize_speech", AsyncMock(return_value=_wav_bytes())
        ):
            async with client as c:
                response = await c.post(
                    "/api/voice/speak", json={"text": "Assignment due Friday."}, cookies=cookies,
                )
        assert response.status_code == 200
        assert response.headers["content-type"] == "audio/wav"
        assert response.content

    async def test_empty_text_is_rejected_by_validation(self, client, cookies):
        async with client as c:
            response = await c.post("/api/voice/speak", json={"text": ""}, cookies=cookies)
        assert response.status_code == 422

    async def test_upstream_failure_returns_502_not_500(self, client, cookies):
        from app.intelligence.llm_client import LLMError

        with patch(
            "app.api.voice.synthesize_speech", AsyncMock(side_effect=LLMError("groq tts: down"))
        ):
            async with client as c:
                response = await c.post(
                    "/api/voice/speak", json={"text": "hello"}, cookies=cookies,
                )
        assert response.status_code == 502
