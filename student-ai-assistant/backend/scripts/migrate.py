#!/usr/bin/env python3
"""
Migration runner.

Applies every `migrations/NNN_*.sql` in filename order, records each in
`schema_migrations`, and skips ones already applied.

The previous runner split files on ";" and fed the pieces to PostgREST. That
shreds any `$$ ... $$` function body — every plpgsql definition in the schema
would have been submitted as several invalid fragments. asyncpg accepts a whole
script in one `execute()` call, so no splitting is needed at all.

Usage:
    python scripts/migrate.py              # apply pending migrations
    python scripts/migrate.py --status     # list applied vs pending
    python scripts/migrate.py --reset      # DROP the schema, then re-apply (dev only)
"""

import argparse
import asyncio
import hashlib
import os
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_DIR))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(BACKEND_DIR / ".env")

import asyncpg  # noqa: E402

MIGRATIONS_DIR = BACKEND_DIR / "migrations"

GREEN, YELLOW, RED, DIM, RESET = "\033[32m", "\033[33m", "\033[31m", "\033[2m", "\033[0m"


def database_url() -> str:
    url = os.environ.get("DATABASE_URL", "").strip()
    if not url:
        sys.exit(
            f"{RED}DATABASE_URL is not set.{RESET}\n"
            "  Local:    postgresql://studentai:studentai@localhost:5432/studentai\n"
            "  Supabase: Project Settings → Database → Connection string (URI)\n"
        )
    # asyncpg does not understand the SQLAlchemy-style +driver suffix.
    return url.replace("postgresql+asyncpg://", "postgresql://")


def migration_files() -> list[Path]:
    files = sorted(p for p in MIGRATIONS_DIR.glob("*.sql") if not p.name.startswith("_"))
    if not files:
        sys.exit(f"{RED}No migrations found in {MIGRATIONS_DIR}{RESET}")
    return files


def checksum(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()[:16]


async def ensure_ledger(conn: asyncpg.Connection) -> None:
    await conn.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_migrations (
            version    TEXT PRIMARY KEY,
            checksum   TEXT NOT NULL,
            applied_at TIMESTAMPTZ NOT NULL DEFAULT now()
        );
        """
    )


async def applied_versions(conn: asyncpg.Connection) -> dict[str, str]:
    rows = await conn.fetch("SELECT version, checksum FROM schema_migrations")
    return {r["version"]: r["checksum"] for r in rows}


async def cmd_status(conn: asyncpg.Connection) -> int:
    await ensure_ledger(conn)
    applied = await applied_versions(conn)

    print(f"\n{DIM}migration                        status{RESET}")
    print(f"{DIM}{'─' * 56}{RESET}")
    pending = 0
    for path in migration_files():
        version = path.stem
        if version not in applied:
            print(f"{path.name:<33}{YELLOW}pending{RESET}")
            pending += 1
        elif applied[version] != checksum(path):
            print(f"{path.name:<33}{RED}MODIFIED since apply{RESET}")
        else:
            print(f"{path.name:<33}{GREEN}applied{RESET}")
    print()
    return pending


async def cmd_reset(conn: asyncpg.Connection) -> None:
    if os.environ.get("APP_ENV") == "production":
        sys.exit(f"{RED}Refusing to --reset with APP_ENV=production.{RESET}")

    host = database_url().split("@")[-1].split("/")[0]
    print(f"{RED}This DROPs the entire public schema on {host}{RESET}")
    if input("Type 'yes' to continue: ").strip() != "yes":
        sys.exit("Aborted.")

    await conn.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public;")
    print(f"{YELLOW}Schema dropped.{RESET}")


async def apply_pending(conn: asyncpg.Connection) -> None:
    await ensure_ledger(conn)
    applied = await applied_versions(conn)

    todo = [p for p in migration_files() if p.stem not in applied]
    if not todo:
        print(f"{GREEN}✓ Database is up to date — nothing to apply.{RESET}")
        return

    for path in todo:
        version = path.stem
        print(f"  applying {path.name} … ", end="", flush=True)
        sql = path.read_text()
        try:
            # One transaction per migration: a failure leaves no partial schema.
            async with conn.transaction():
                await conn.execute(sql)
                await conn.execute(
                    "INSERT INTO schema_migrations (version, checksum) VALUES ($1, $2)",
                    version,
                    checksum(path),
                )
        except Exception as exc:
            print(f"{RED}FAILED{RESET}")
            print(f"\n{RED}{type(exc).__name__}: {exc}{RESET}\n")
            raise SystemExit(1)
        print(f"{GREEN}ok{RESET}")

    print(f"\n{GREEN}✓ Applied {len(todo)} migration(s).{RESET}")


async def main() -> None:
    parser = argparse.ArgumentParser(description="Apply database migrations.")
    parser.add_argument("--status", action="store_true", help="show applied vs pending")
    parser.add_argument("--reset", action="store_true", help="drop schema then re-apply (dev only)")
    args = parser.parse_args()

    url = database_url()
    try:
        conn = await asyncpg.connect(url, timeout=15)
    except Exception as exc:
        host = url.split("@")[-1].split("/")[0]
        sys.exit(
            f"{RED}Could not connect to {host}{RESET}\n"
            f"  {type(exc).__name__}: {exc}\n\n"
            "  Local dev:  docker compose up -d db\n"
            "  Supabase:   check the project is not paused\n"
        )

    try:
        if args.status:
            await cmd_status(conn)
            return
        if args.reset:
            await cmd_reset(conn)
        await apply_pending(conn)

        # HNSW and the FTS planner both rely on fresh statistics.
        await conn.execute("ANALYZE;")
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
