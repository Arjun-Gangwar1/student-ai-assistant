"""
Security regressions.

Each test corresponds to a specific vulnerability found in the audit. They exist
so those cannot come back quietly.
"""

import base64
import json

import itsdangerous
import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.db import queries
from app.utils.crypto import TokenEncryptionError, decrypt_tokens, encrypt_tokens, redact


class TestTokenEncryption:
    """
    google_tokens was plaintext JSONB. Refresh tokens do not expire, so one
    database read granted persistent Gmail access with no alert to the student.
    """

    def test_roundtrip(self):
        tokens = {"access_token": "ya29.abc", "refresh_token": "1//xyz", "scopes": ["a", "b"]}
        assert decrypt_tokens(encrypt_tokens(tokens)) == tokens

    def test_ciphertext_does_not_contain_plaintext(self):
        blob = encrypt_tokens({"refresh_token": "SUPER_SECRET_VALUE"})
        assert "SUPER_SECRET_VALUE" not in blob

    def test_none_and_empty_are_not_errors(self):
        """Distinguishable from a decryption failure — one is routine."""
        assert decrypt_tokens(None) is None
        assert decrypt_tokens("") is None

    def test_tampering_is_detected(self):
        blob = encrypt_tokens({"refresh_token": "x"})
        tampered = blob[:-8] + "AAAAAAAA"
        with pytest.raises(TokenEncryptionError):
            decrypt_tokens(tampered)

    def test_wrong_key_is_detected(self):
        from cryptography.fernet import Fernet

        import app.utils.crypto as crypto

        blob = encrypt_tokens({"refresh_token": "x"})
        original, crypto._fernet = crypto._fernet, Fernet(Fernet.generate_key())
        try:
            with pytest.raises(TokenEncryptionError):
                decrypt_tokens(blob)
        finally:
            crypto._fernet = original

    def test_redact_never_reveals_the_secret(self):
        # Synthetic value. Never use a fragment of a real credential as test
        # data — it ends up in git history, which is exactly what this file
        # exists to prevent.
        secret = "gsk_FAKEKEY0123456789abcdefghijklmnop"
        assert secret[4:] not in redact(secret)
        assert redact(secret).startswith("gsk_")
        assert redact(None) == "<empty>"


@pytest.mark.db
class TestTokensAtRest:
    async def test_database_column_holds_ciphertext(self, student):
        from app.db.pool import get_pool

        student_id = str(student["id"])
        await queries.set_student_tokens(
            student_id, {"refresh_token": "1//PLAINTEXT_MARKER", "access_token": "ya29.x"}
        )

        async with get_pool().acquire() as conn:
            stored = await conn.fetchval(
                "SELECT google_tokens_enc FROM students WHERE id = $1", student_id
            )

        assert "PLAINTEXT_MARKER" not in stored
        assert "refresh_token" not in stored

        recovered = await queries.get_student_with_tokens(student_id)
        assert recovered["google_tokens"]["refresh_token"] == "1//PLAINTEXT_MARKER"

    async def test_public_reads_never_expose_tokens(self, student):
        """The default student read must not carry credentials into a response."""
        fetched = await queries.get_student(str(student["id"]))
        assert "google_tokens" not in fetched
        assert "google_tokens_enc" not in fetched


@pytest.mark.db
class TestEndpointAuthentication:
    """
    /api/deadlines/{student_id}, /api/items/{student_id} and /chat/ask took the
    subject of the request from the caller and never checked the session.
    """

    @pytest.fixture
    def client(self, pool):
        from app.main import app

        # Skip lifespan: the pool fixture already owns the connection.
        return TestClient(app, raise_server_exceptions=False)

    @pytest.mark.parametrize(
        "method,path",
        [
            ("GET", "/api/deadlines"),
            ("GET", "/api/items"),
            ("GET", "/api/emails"),
            ("GET", "/api/emails/search?q=x"),
            ("GET", "/api/auth/me"),
            ("GET", "/api/auth/profile"),
            ("GET", "/api/auth/export"),
            ("GET", "/api/sync/status"),
            ("GET", "/api/chat/quota"),
            ("POST", "/api/chat/ask"),
            ("POST", "/api/sync/now"),
            ("POST", "/api/deadlines/feedback"),
            ("POST", "/api/auth/telegram/link-token"),
            ("DELETE", "/api/auth/account"),
            ("PATCH", "/api/items/abc/read"),
            ("PATCH", "/api/deadlines/abc/confirm"),
        ],
    )
    def test_requires_authentication(self, client, method, path):
        response = client.request(method, path, json={})
        assert response.status_code == 401, f"{method} {path} returned {response.status_code}"

    def test_forged_session_signature_rejected(self, client):
        payload = base64.b64encode(json.dumps({"student_id": "11111111-1111-1111-1111-111111111111"}).encode())
        forged = itsdangerous.TimestampSigner("wrong-signing-key").sign(payload).decode()
        response = client.get("/api/deadlines", cookies={"studentai_session": forged})
        assert response.status_code == 401

    async def test_valid_signature_for_deleted_student_rejected(self, pool):
        """
        A session must stop working the moment the account is gone — which is
        why get_current_student re-reads the row instead of trusting the cookie.

        Uses an async client rather than TestClient: TestClient drives the app on
        its own event loop, and the asyncpg pool here belongs to the test's loop.
        """
        import httpx

        from app.main import app

        payload = base64.b64encode(
            json.dumps({"student_id": "11111111-1111-1111-1111-111111111111"}).encode()
        )
        cookie = itsdangerous.TimestampSigner(settings.secret_key).sign(payload).decode()

        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get(
                "/api/deadlines", cookies={"studentai_session": cookie}
            )
        assert response.status_code == 401


class TestSecretsHygiene:
    def test_env_example_has_no_live_credentials(self):
        """
        .env.example held real Supabase, Google, Groq and Telegram credentials
        and was not gitignored.
        """
        from pathlib import Path
        import re

        text = (Path(__file__).resolve().parent.parent / ".env.example").read_text()
        patterns = [
            (r"gsk_[A-Za-z0-9]{40,}", "Groq key"),
            (r"GOCSPX-[A-Za-z0-9_\-]{20,}", "Google secret"),
            (r"\d{8,12}:AA[A-Za-z0-9_\-]{30,}", "Telegram token"),
            (r"eyJ[A-Za-z0-9_\-]{10,}\.eyJ[A-Za-z0-9_\-]{20,}\.", "JWT"),
        ]
        for pattern, label in patterns:
            assert not re.search(pattern, text), f"{label} present in .env.example"
