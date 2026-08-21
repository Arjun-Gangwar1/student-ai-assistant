"""
Test fixtures.

A real Postgres is started via `pgserver` — an embedded build that needs no
Docker and no root. Testing the database layer against SQLite or a mock would be
close to pointless here: the parts most worth testing are pgvector operators,
generated tsvector columns, ON CONFLICT semantics and RRF fusion, none of which
exist outside real Postgres.
"""

import asyncio
import os
import sys
from pathlib import Path

import pytest
import pytest_asyncio

BACKEND_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_DIR))

# Must be set before app.config is imported anywhere.
os.environ.setdefault("GOOGLE_CLIENT_ID", "test-client-id")
os.environ.setdefault("GOOGLE_CLIENT_SECRET", "test-client-secret")
os.environ.setdefault("SECRET_KEY", "test-secret-key-at-least-32-characters-long")
os.environ.setdefault("TOKEN_ENCRYPTION_KEY", "")   # filled in below
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "000:test")
os.environ.setdefault("APP_ENV", "development")

from cryptography.fernet import Fernet  # noqa: E402

if not os.environ.get("TOKEN_ENCRYPTION_KEY"):
    os.environ["TOKEN_ENCRYPTION_KEY"] = Fernet.generate_key().decode()

PG_DATA_DIR = Path(os.environ.get("TEST_PGDATA", "/tmp/studentai-test-pg"))


@pytest.fixture(scope="session")
def database_url() -> str:
    """Start (or reuse) an embedded Postgres and return its DSN."""
    import pgserver

    PG_DATA_DIR.mkdir(parents=True, exist_ok=True)
    server = pgserver.get_server(str(PG_DATA_DIR), cleanup_mode=None)
    uri = server.get_uri()

    os.environ["DATABASE_URL"] = uri
    # `settings` is instantiated at import time from .env, which points at the
    # development database. Setting the environment variable alone is too late,
    # so override the live object — otherwise tests silently run against (or
    # fail to reach) whatever DATABASE_URL happens to be configured locally.
    from app.config import settings

    settings.database_url = uri
    return uri


@pytest.fixture(scope="session")
def migrated_db(database_url: str) -> str:
    """
    Apply every migration once per session.

    Deliberately a *sync* fixture running its own short-lived loop. A
    session-scoped async fixture would bind its connection to a loop that the
    function-scoped tests do not share, producing "attached to a different
    loop" from asyncpg. Nothing is shared out of here but a connection string.
    """
    import asyncpg

    async def _apply() -> None:
        conn = await asyncpg.connect(database_url)
        try:
            # Clean slate — a leftover schema from a previous run would mask
            # exactly the migration bugs these tests exist to catch.
            await conn.execute("DROP SCHEMA IF EXISTS public CASCADE; CREATE SCHEMA public;")
            for path in sorted((BACKEND_DIR / "migrations").glob("*.sql")):
                if path.name.startswith("_"):
                    continue
                await conn.execute(path.read_text())
        finally:
            await conn.close()

    asyncio.run(_apply())
    return database_url


@pytest_asyncio.fixture
async def pool(migrated_db):
    """A live asyncpg pool, torn down between tests."""
    from app.db import pool as pool_module

    await pool_module.close_pool()
    created = await pool_module.init_pool()
    yield created
    await pool_module.close_pool()


@pytest_asyncio.fixture
async def clean_db(pool):
    """Empty every table before each test."""
    async with pool.acquire() as conn:
        await conn.execute("TRUNCATE students CASCADE")
    return pool


@pytest_asyncio.fixture
async def student(clean_db) -> dict:
    from app.db import queries

    created = await queries.upsert_student(
        google_id="test_google_id",
        email="test@iitdh.ac.in",
        name="Test Student",
        scopes=["https://www.googleapis.com/auth/classroom.courses.readonly"],
        consent_version="2026-08-21",
    )
    await queries.update_student_profile(str(created["id"]), year=2, branch="CS")
    return await queries.get_student(str(created["id"]))


@pytest.fixture
def anyio_backend():
    return "asyncio"
