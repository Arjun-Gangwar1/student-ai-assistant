"""
FastAPI application entry point.
Registers all routers and lifecycle events.
"""

import os
import logging
from contextlib import asynccontextmanager

# Allow http:// redirect URIs during local development only
if os.environ.get("APP_ENV", "development") != "production":
    os.environ.setdefault("OAUTHLIB_INSECURE_TRANSPORT", "1")

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.sessions import SessionMiddleware

from app.config import settings
from app.api import auth, chat, deadlines, items, telegram_webhook, sync, emails
from app.workers.sync_worker import get_scheduler
from app.alerts.telegram_bot import set_webhook

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # ── Startup ────────────────────────────────────────────────────────────
    logger.info("Starting Student AI Assistant backend...")

    scheduler = get_scheduler()
    scheduler.start()
    logger.info("Background scheduler started")

    # Register Telegram webhook in production
    if settings.app_env == "production" and settings.telegram_bot_token:
        webhook_url = f"{settings.backend_url}/api/telegram/webhook"
        await set_webhook(webhook_url)

    yield

    # ── Shutdown ───────────────────────────────────────────────────────────
    scheduler.shutdown(wait=False)
    logger.info("Scheduler stopped")


app = FastAPI(
    title="Student AI Assistant API",
    description="Real-time personal AI assistant for IIT Dharwad students",
    version="1.0.0",
    lifespan=lifespan,
)

# ── Middleware ─────────────────────────────────────────────────────────────────
app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.frontend_url],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
is_prod = settings.app_env == "production"
app.add_middleware(
    SessionMiddleware,
    secret_key=settings.secret_key,
    https_only=is_prod,
    same_site="none" if is_prod else "lax",
)

# ── Routers ────────────────────────────────────────────────────────────────────
app.include_router(auth.router)
app.include_router(chat.router)
app.include_router(deadlines.router)
app.include_router(items.router)
app.include_router(telegram_webhook.router)
app.include_router(sync.router)
app.include_router(emails.router)


@app.get("/health")
async def health():
    return {"status": "ok", "service": "student-ai-assistant"}
