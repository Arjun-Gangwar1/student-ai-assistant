"""
FastAPI entry point.
"""

import logging
import os
from contextlib import asynccontextmanager

# oauthlib refuses http:// redirect URIs unless told otherwise. Local dev needs
# http://localhost; production must never set this, so it is gated on APP_ENV
# before any oauthlib import happens.
if os.environ.get("APP_ENV", "development") != "production":
    os.environ.setdefault("OAUTHLIB_INSECURE_TRANSPORT", "1")

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.middleware.sessions import SessionMiddleware

from app.api import auth, chat, deadlines, emails, items, sync, telegram_webhook, voice
from app.config import settings
from app.db.pool import close_pool, init_pool
from app.intelligence.embedder import warm_up
from app.workers.sync_worker import get_scheduler

logging.basicConfig(
    level=getattr(logging, settings.log_level.upper(), logging.INFO),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
# These two log every HTTP call they make at INFO, which drowns everything else.
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("googleapiclient.discovery_cache").setLevel(logging.ERROR)

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Starting Student AI Assistant (env=%s)", settings.app_env)

    # Configuration problems that are syntactically valid but will fail in use.
    # Fatal in production, a warning in development so local work is possible
    # with a partial .env.
    if problems := settings.validate_runtime():
        for problem in problems:
            logger.warning("Config: %s", problem)
        if settings.is_production:
            raise RuntimeError(
                "Refusing to start in production with configuration problems:\n  - "
                + "\n  - ".join(problems)
            )

    await init_pool()

    scheduler = get_scheduler()
    scheduler.start()
    logger.info("Scheduler started with %d job(s)", len(scheduler.get_jobs()))

    # Load the embedding model now rather than during a student's first question.
    await warm_up()

    if settings.is_production and settings.telegram_bot_token:
        from app.alerts.telegram_bot import set_webhook

        await set_webhook(f"{settings.backend_url}/api/telegram/webhook")

    logger.info("Ready")
    yield

    scheduler.shutdown(wait=False)
    await close_pool()
    logger.info("Shutdown complete")


app = FastAPI(
    title="Student AI Assistant API",
    description="Deadline radar and Q&A for IIT Dharwad students",
    version="1.1.0",
    lifespan=lifespan,
    # The interactive docs describe every endpoint including auth flows; useful
    # in development, an unnecessary disclosure in production.
    docs_url=None if settings.is_production else "/docs",
    redoc_url=None,
)

# ── Middleware ───────────────────────────────────────────────────────────────
app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.frontend_url],
    allow_credentials=True,     # session cookie must be sent cross-origin
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Content-Type", "Authorization"],
)

app.add_middleware(
    SessionMiddleware,
    secret_key=settings.secret_key,
    session_cookie="studentai_session",
    max_age=30 * 24 * 3600,
    https_only=settings.is_production,
    # Frontend and backend sit on different origins in production (Vercel /
    # Railway), so the cookie must be SameSite=None — which requires Secure,
    # hence the pairing with https_only.
    same_site="none" if settings.is_production else "lax",
)


@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    if settings.is_production:
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    return response


@app.exception_handler(Exception)
async def unhandled_exception(request: Request, exc: Exception):
    """
    Log the detail, return a generic message.

    Stack traces and driver errors in an HTTP response leak schema and
    configuration to anyone who can trigger a 500.
    """
    logger.exception("Unhandled error on %s %s", request.method, request.url.path)
    return JSONResponse(
        status_code=500,
        content={"detail": "Something went wrong. Please try again."},
    )


# ── Routers ──────────────────────────────────────────────────────────────────
for router in (auth, chat, deadlines, items, emails, sync, telegram_webhook, voice):
    app.include_router(router.router)


@app.get("/health", tags=["ops"])
async def health():
    """Liveness plus a real database round trip — Railway polls this."""
    from app.db.pool import acquire

    database_ok = True
    try:
        async with acquire() as conn:
            await conn.fetchval("SELECT 1")
    except Exception as exc:
        logger.error("Health check: database unreachable: %s", exc)
        database_ok = False

    return JSONResponse(
        status_code=200 if database_ok else 503,
        content={
            "status": "ok" if database_ok else "degraded",
            "service": "student-ai-assistant",
            "version": app.version,
            "database": "up" if database_ok else "down",
        },
    )
