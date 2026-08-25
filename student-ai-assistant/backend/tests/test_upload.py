"""
Chat document upload — extract, answer immediately, index for later search.

LLM calls (classification and answer generation) and the embedder are mocked:
this suite verifies the endpoint's own logic (validation, persistence, message
history), not Groq's output or the local model's vectors — those are covered
in test_extraction.py and exercised for real by hand against the live app.

Uses httpx.AsyncClient over ASGITransport rather than TestClient: TestClient
drives the app on its own event loop, and the asyncpg `pool` fixture here
belongs to the test's loop — a request that actually reaches the database
(anything past an unauthenticated 401) needs to run on that same loop or the
connection breaks mid-query. See test_security.py's identical note.
"""

import base64
import json
from unittest.mock import AsyncMock, patch

import httpx
import itsdangerous
import pytest

from app.config import settings
from app.db import queries


def _session_cookie(student_id: str) -> str:
    payload = base64.b64encode(json.dumps({"student_id": student_id}).encode())
    return itsdangerous.TimestampSigner(settings.secret_key).sign(payload).decode()


@pytest.fixture
def client(pool):
    from app.main import app

    transport = httpx.ASGITransport(app=app)
    return httpx.AsyncClient(transport=transport, base_url="http://test")


@pytest.fixture
def cookies(student):
    return {"studentai_session": _session_cookie(str(student["id"]))}


@pytest.fixture(autouse=True)
def mocked_llm_and_embedder():
    """
    Patch every module-local `llm` reference plus the embedder, so this suite
    never makes a network call or loads the local sentence-transformers model.
    """
    llm_client = AsyncMock()
    llm_client.model = "test-model"
    llm_client.chat = AsyncMock(
        return_value=json.dumps({
            "category": "academic", "priority": "MEDIUM",
            "relevance": 0.6, "one_line_summary": "Test summary",
        })
    )

    fake_embed = AsyncMock(return_value=[0.0] * 768)
    patches = [
        patch("app.intelligence.classifier.llm", return_value=llm_client),
        patch("app.rag.generator.llm", return_value=llm_client),
        # Two independent references — uploads.py embeds the new document,
        # retriever.py embeds the query when answer_about_document also runs
        # normal retrieval for supporting context.
        patch("app.intelligence.uploads.embed_text", fake_embed),
        patch("app.rag.retriever.embed_text", fake_embed),
    ]
    for p in patches:
        p.start()
    yield llm_client
    for p in patches:
        p.stop()


@pytest.mark.db
class TestUploadValidation:
    async def test_unsupported_extension_is_rejected(self, client, cookies):
        async with client as c:
            response = await c.post(
                "/api/chat/upload",
                files={"file": ("photo.png", b"not text", "image/png")},
                cookies=cookies,
            )
        assert response.status_code == 415

    async def test_empty_text_file_is_rejected(self, client, cookies):
        async with client as c:
            response = await c.post(
                "/api/chat/upload",
                files={"file": ("blank.txt", b"   \n\n  ", "text/plain")},
                cookies=cookies,
            )
        assert response.status_code == 422

    async def test_requires_authentication(self, client):
        async with client as c:
            response = await c.post(
                "/api/chat/upload",
                files={"file": ("notes.txt", b"hello", "text/plain")},
            )
        assert response.status_code == 401


@pytest.mark.db
class TestUploadFlow:
    async def test_upload_answers_and_indexes_the_document(self, client, cookies, student):
        async with client as c:
            response = await c.post(
                "/api/chat/upload",
                files={"file": ("syllabus.txt", b"Assignment 3 is due Friday.", "text/plain")},
                data={"question": "When is Assignment 3 due?"},
                cookies=cookies,
            )
        assert response.status_code == 200
        body = response.json()
        assert body["answer"]
        assert body["conversation_id"]
        # The document itself is always the first source, ahead of anything
        # retrieval found — the point of answer_about_document is that the
        # upload is never outranked by unrelated context.
        assert body["sources"][0]["source"] == "upload"
        assert body["sources"][0]["title"] == "syllabus.txt"

        item = await queries.get_item(body["sources"][0]["id"], str(student["id"]))
        assert item is not None
        assert item["source"] == "upload"
        assert "Assignment 3" in item["raw_content"]
        assert item["category"] == "academic"        # from the mocked classification

        messages = await queries.get_messages(body["conversation_id"], str(student["id"]))
        assert [m["role"] for m in messages] == ["user", "assistant"]
        assert "syllabus.txt" in messages[0]["content"]

    async def test_upload_defaults_the_question_when_none_given(self, client, cookies):
        async with client as c:
            response = await c.post(
                "/api/chat/upload",
                files={"file": ("notice.txt", b"Hostel maintenance on Sunday.", "text/plain")},
                cookies=cookies,
            )
        assert response.status_code == 200
        # A default question is used rather than sending an empty one to the LLM.
        assert response.json()["answer"]
