"""
Encryption for Google OAuth tokens at rest.

Google refresh tokens do not expire on their own. Stored in plaintext — as the
previous `students.google_tokens` JSONB column did, despite a schema comment
claiming otherwise — a single database read grants persistent access to every
connected student's Gmail, Classroom and Calendar, with no login alert on their
side. Encrypting the column means a database leak alone is not enough; the
attacker also needs TOKEN_ENCRYPTION_KEY, which lives only in the environment.

Fernet: AES-128-CBC with an HMAC-SHA256 authentication tag, from `cryptography`.
Authenticated, so tampering is detected rather than silently decrypted.
"""

import json
import logging

from cryptography.fernet import Fernet, InvalidToken

from app.config import settings

logger = logging.getLogger(__name__)


class TokenEncryptionError(RuntimeError):
    """Raised when tokens cannot be encrypted or decrypted."""


_fernet: Fernet | None = None


def _get_fernet() -> Fernet:
    global _fernet
    if _fernet is None:
        key = settings.token_encryption_key.strip()
        if not key:
            raise TokenEncryptionError(
                "TOKEN_ENCRYPTION_KEY is not set. Generate one with:\n"
                '  python3 -c "from cryptography.fernet import Fernet; '
                'print(Fernet.generate_key().decode())"\n'
                "Store it in .env and back it up — losing it makes every stored "
                "OAuth token undecryptable and forces all users to re-authenticate."
            )
        try:
            _fernet = Fernet(key.encode())
        except (ValueError, TypeError) as exc:
            raise TokenEncryptionError(
                f"TOKEN_ENCRYPTION_KEY is not a valid Fernet key ({exc}). "
                "It must be 32 url-safe base64-encoded bytes."
            ) from exc
    return _fernet


def encrypt_tokens(tokens: dict) -> str:
    """Serialise and encrypt an OAuth token dict for storage."""
    if not isinstance(tokens, dict):
        raise TokenEncryptionError(f"expected dict, got {type(tokens).__name__}")
    payload = json.dumps(tokens, separators=(",", ":")).encode()
    return _get_fernet().encrypt(payload).decode()


def decrypt_tokens(blob: str | None) -> dict | None:
    """
    Decrypt a stored token blob. Returns None when there is nothing stored, and
    raises only on a key mismatch or tampering — the caller must be able to tell
    "this student never connected Google" apart from "we cannot read their
    tokens", because the second is an incident and the first is routine.
    """
    if not blob:
        return None
    try:
        raw = _get_fernet().decrypt(blob.encode())
    except InvalidToken as exc:
        raise TokenEncryptionError(
            "Stored OAuth tokens could not be decrypted. Either TOKEN_ENCRYPTION_KEY "
            "changed since they were written, or the row was tampered with. "
            "Affected students must reconnect their Google account."
        ) from exc
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise TokenEncryptionError("Decrypted token blob is not valid JSON") from exc


def redact(value: str | None, keep: int = 4) -> str:
    """Render a secret safe for logs: 'gsk_WW8v…' rather than the whole key."""
    if not value:
        return "<empty>"
    if len(value) <= keep:
        return "…"
    return f"{value[:keep]}…({len(value)} chars)"
