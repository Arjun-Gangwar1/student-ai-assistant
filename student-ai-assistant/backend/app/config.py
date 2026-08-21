from pydantic_settings import BaseSettings
from functools import lru_cache


class Settings(BaseSettings):
    # Google OAuth
    google_client_id: str
    google_client_secret: str
    google_redirect_uri: str = "http://localhost:8000/api/auth/callback"

    # Supabase
    supabase_url: str
    supabase_anon_key: str
    supabase_service_role_key: str

    # LLM
    groq_api_key: str
    gemini_api_key: str
    openai_api_key: str
    deepinfra_api_key: str = ""

    # Telegram
    telegram_bot_token: str
    telegram_webhook_secret: str = ""

    # App
    app_env: str = "development"
    secret_key: str
    frontend_url: str = "http://localhost:3000"
    backend_url: str = "http://localhost:8000"

    # Redis
    redis_url: str = "redis://localhost:6379"

    # Polling
    classroom_poll_interval_minutes: int = 120
    calendar_poll_interval_minutes: int = 120
    website_scrape_interval_minutes: int = 360
    digest_send_time: str = "07:30"

    # Embedding
    embedding_model: str = "all-mpnet-base-v2"
    embedding_dimensions: int = 768

    class Config:
        env_file = ".env"
        case_sensitive = False


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
