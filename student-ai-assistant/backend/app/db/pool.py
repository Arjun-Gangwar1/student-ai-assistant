"""
asyncpg connection pool.

Replaces the supabase-py (PostgREST) client. Three reasons the swap was worth
making rather than a matter of taste:

  1. Local Postgres and Supabase become the same code path. Supabase *is*
     Postgres; only the connection string differs. The previous setup could not
     run at all without a live Supabase project, which is exactly how this
     project came to be blocked when the free-tier project was reaped.
  2. Real SQL. Hybrid search, RRF fusion and `ON CONFLICT` upserts are one
     statement each here and are impossible-to-awkward over PostgREST.
  3. One round trip instead of several. The PostgREST layer forced N+1 patterns
     — `get_deadlines_needing_alert` fetched students separately per deadline.
"""

import json
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import asyncpg

from app.config import settings

logger = logging.getLogger(__name__)

_pool: asyncpg.Pool | None = None


async def _init_connection(conn: asyncpg.Connection) -> None:
    """
    Per-connection setup.

    JSONB is registered as a codec so `metadata` round-trips as a dict rather
    than a string — without this every caller has to remember json.loads, and
    one that forgets stores a JSON-encoded string inside a JSONB column.
    """
    await conn.set_type_codec(
        "jsonb",
        encoder=json.dumps,
        decoder=json.loads,
        schema="pg_catalog",
    )
    await conn.set_type_codec(
        "json",
        encoder=json.dumps,
        decoder=json.loads,
        schema="pg_catalog",
    )


async def init_pool() -> asyncpg.Pool:
    """Create the pool. Called once from the FastAPI lifespan handler."""
    global _pool
    if _pool is not None:
        return _pool

    try:
        _pool = await asyncpg.create_pool(
            dsn=settings.asyncpg_dsn,
            min_size=settings.db_pool_min_size,
            max_size=settings.db_pool_max_size,
            command_timeout=30,
            init=_init_connection,
            # Supabase's pooler (pgbouncer in transaction mode) cannot use
            # server-side prepared statements; disabling the cache keeps the
            # same code working against both a direct connection and the pooler.
            statement_cache_size=0,
        )
    except Exception as exc:
        host = settings.asyncpg_dsn.split("@")[-1].split("/")[0]
        logger.error("Database connection failed (%s): %s", host, exc)
        raise

    async with _pool.acquire() as conn:
        version = await conn.fetchval("SHOW server_version")
    logger.info("Database pool ready (Postgres %s)", version)
    return _pool


async def close_pool() -> None:
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None
        logger.info("Database pool closed")


def get_pool() -> asyncpg.Pool:
    if _pool is None:
        raise RuntimeError(
            "Database pool is not initialised. init_pool() runs in the FastAPI "
            "lifespan handler; standalone scripts must call it themselves."
        )
    return _pool


@asynccontextmanager
async def acquire() -> AsyncIterator[asyncpg.Connection]:
    """`async with acquire() as conn:` — a pooled connection."""
    async with get_pool().acquire() as conn:
        yield conn


@asynccontextmanager
async def transaction() -> AsyncIterator[asyncpg.Connection]:
    """`async with transaction() as conn:` — a connection inside a transaction."""
    async with get_pool().acquire() as conn:
        async with conn.transaction():
            yield conn


def to_vector_literal(vec: list[float]) -> str:
    """
    pgvector's text input format. Sent as a string and cast with `$n::vector`,
    which avoids depending on a registered vector codec.
    """
    return "[" + ",".join(f"{v:.7g}" for v in vec) + "]"
