"""
Application settings, loaded from .env via pydantic-settings.

Anything secret belongs here and nowhere else — no module should read os.environ
directly, so that the full set of required configuration is visible in one place
and the app fails loudly at startup rather than at first use.
"""

from functools import lru_cache

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ── Google OAuth ─────────────────────────────────────────────────────────
    google_client_id: str
    google_client_secret: str
    google_redirect_uri: str = "http://localhost:8000/api/auth/callback"

    # ── Database ─────────────────────────────────────────────────────────────
    # One Postgres URL for every environment. Supabase is Postgres, so local dev
    # and production differ only by connection string — no PostgREST in between.
    database_url: str
    db_pool_min_size: int = 2
    db_pool_max_size: int = 10

    # ── LLM ──────────────────────────────────────────────────────────────────
    # Provider selection is its own variable. It used to be inferred from
    # app_env == "fallback", which made the fallback provider unreachable in
    # production — precisely where a provider outage matters.
    llm_provider: str = "groq"          # groq | deepinfra
    groq_api_key: str = ""
    gemini_api_key: str = ""
    deepinfra_api_key: str = ""

    # Model ids are configuration, not constants. Groq retired the entire
    # llama-3.1 family that this project was built on; the hardcoded
    # "llama-3.1-8b-instant" started returning 404 model_not_found and every
    # classification and answer failed. Overridable per environment so the next
    # decommission is an env change rather than a code change.
    groq_model: str = "openai/gpt-oss-20b"
    # gpt-oss models emit hidden reasoning tokens before answering; that is most
    # of their latency. "low" roughly halves it and costs little on grounded
    # retrieval answers, where the facts are already in the prompt.
    groq_reasoning_effort: str = "low"      # low | medium | high | default
    deepinfra_model: str = "meta-llama/Meta-Llama-3.1-8B-Instruct"

    # ── Voice (STT/TTS, Groq only — see app/intelligence/voice.py) ───────────
    groq_stt_model: str = "whisper-large-v3-turbo"
    groq_tts_model: str = "canopylabs/orpheus-v1-english"
    groq_tts_voice: str = "hannah"

    # ── Telegram ─────────────────────────────────────────────────────────────
    telegram_bot_token: str = ""
    telegram_webhook_secret: str = ""

    # ── App ──────────────────────────────────────────────────────────────────
    app_env: str = "development"        # development | production
    secret_key: str                     # signs session cookies
    token_encryption_key: str = ""      # Fernet key for OAuth tokens at rest
    frontend_url: str = "http://localhost:3000"
    backend_url: str = "http://localhost:8000"
    log_level: str = "INFO"

    # ── Feature flags ────────────────────────────────────────────────────────
    # Gmail is a RESTRICTED OAuth scope: shipping it to real users requires
    # Google verification plus an annual CASA assessment. Keeping it behind a
    # flag means that obligation is a deliberate choice, not a default.
    gmail_enabled: bool = True
    gmail_allowlist: str = ""           # comma-separated; empty = all students

    # ── Redis ────────────────────────────────────────────────────────────────
    redis_url: str = "redis://localhost:6379"

    # ── Polling ──────────────────────────────────────────────────────────────
    gmail_poll_interval_minutes: int = 30
    classroom_poll_interval_minutes: int = 120
    calendar_poll_interval_minutes: int = 120
    website_scrape_interval_minutes: int = 360
    digest_send_time: str = "07:30"     # HH:MM, IST

    # ── Embeddings (local sentence-transformers, no API cost) ────────────────
    embedding_model: str = "all-mpnet-base-v2"
    embedding_dimensions: int = 768

    # ── Rate limits ──────────────────────────────────────────────────────────
    chat_rate_limit_per_day: int = 50
    sync_rate_limit_per_hour: int = 6
    voice_rate_limit_per_day: int = 30

    @property
    def is_production(self) -> bool:
        return self.app_env == "production"

    @property
    def gmail_allowlist_emails(self) -> set[str]:
        return {e.strip().lower() for e in self.gmail_allowlist.split(",") if e.strip()}

    @property
    def asyncpg_dsn(self) -> str:
        """asyncpg rejects the SQLAlchemy-style +driver suffix."""
        return self.database_url.replace("postgresql+asyncpg://", "postgresql://")

    @field_validator("llm_provider")
    @classmethod
    def _known_provider(cls, v: str) -> str:
        allowed = {"groq", "deepinfra"}
        if v not in allowed:
            raise ValueError(f"llm_provider must be one of {sorted(allowed)}, got {v!r}")
        return v

    @field_validator("digest_send_time")
    @classmethod
    def _valid_time(cls, v: str) -> str:
        try:
            hh, mm = v.split(":")
            if not (0 <= int(hh) <= 23 and 0 <= int(mm) <= 59):
                raise ValueError
        except (ValueError, AttributeError):
            raise ValueError(f"digest_send_time must be HH:MM, got {v!r}")
        return v

    def validate_runtime(self) -> list[str]:
        """
        Non-fatal startup warnings for configuration that is syntactically valid
        but will fail in practice. Returned rather than logged so main.py decides
        whether a given gap is fatal for the current environment.
        """
        problems: list[str] = []

        if self.llm_provider == "groq" and not self.groq_api_key:
            problems.append("LLM_PROVIDER=groq but GROQ_API_KEY is empty — Q&A will fail")
        if self.llm_provider == "deepinfra" and not self.deepinfra_api_key:
            problems.append("LLM_PROVIDER=deepinfra but DEEPINFRA_API_KEY is empty")
        if not self.telegram_bot_token:
            problems.append("TELEGRAM_BOT_TOKEN is empty — digests and alerts cannot send")
        if not self.token_encryption_key:
            problems.append(
                "TOKEN_ENCRYPTION_KEY is empty — Google OAuth tokens would be stored "
                "in plaintext. Generate one: "
                'python3 -c "from cryptography.fernet import Fernet; '
                'print(Fernet.generate_key().decode())"'
            )
        if self.is_production:
            if not self.telegram_webhook_secret:
                problems.append("TELEGRAM_WEBHOOK_SECRET is empty in production — webhook is unauthenticated")
            if self.frontend_url.startswith("http://"):
                problems.append(f"FRONTEND_URL is not HTTPS in production: {self.frontend_url}")
            if len(self.secret_key) < 32:
                problems.append("SECRET_KEY is shorter than 32 chars in production")
        return problems


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
