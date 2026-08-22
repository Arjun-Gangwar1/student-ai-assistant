#!/usr/bin/env python3
"""
Local development database — a real Postgres, without Docker or root.

Uses `pgserver`, which ships an actual PostgreSQL 16 build with pgvector. Handy
when Docker is unavailable (daemon needs group membership or a sudo password),
and it starts in about a second.

    python scripts/dev_db.py start     # start it and write DATABASE_URL into .env
    python scripts/dev_db.py status    # is it running, and what is in it
    python scripts/dev_db.py stop      # shut it down (data is kept)
    python scripts/dev_db.py reset     # delete all data and re-migrate

The data directory lives outside the repo so a `git clean` cannot wipe it.

This is for development. For production use Supabase or any managed Postgres —
only DATABASE_URL changes, because the app talks plain Postgres either way.
"""

import argparse
import asyncio
import re
import subprocess
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_DIR))

PGDATA = Path.home() / ".local" / "share" / "studentai-devdb"
ENV_FILE = BACKEND_DIR / ".env"

GREEN, YELLOW, RED, DIM, RESET = "\033[32m", "\033[33m", "\033[31m", "\033[2m", "\033[0m"


def get_server(create: bool = True):
    try:
        import pgserver
    except ImportError:
        sys.exit(
            f"{RED}pgserver is not installed.{RESET}\n"
            f"  pip install pgserver\n"
        )
    if not create and not PGDATA.exists():
        return None
    PGDATA.mkdir(parents=True, exist_ok=True)
    # cleanup_mode=None keeps the server alive after this process exits —
    # otherwise it would stop the moment the script finished.
    return pgserver.get_server(str(PGDATA), cleanup_mode=None)


def write_database_url(uri: str) -> None:
    """Point .env at the dev server, preserving everything else."""
    text = ENV_FILE.read_text()
    line = f"DATABASE_URL={uri}"
    if re.search(r"^DATABASE_URL=", text, re.M):
        current = re.search(r"^DATABASE_URL=(.*)$", text, re.M).group(1).strip()
        if current == uri:
            return
        # Keep the previous value as a comment — it is usually the Supabase or
        # docker URL the developer will want back later.
        text = re.sub(
            r"^DATABASE_URL=.*$",
            f"# previous: DATABASE_URL={current}\n{line}",
            text,
            count=1,
            flags=re.M,
        )
    else:
        text = text.rstrip() + f"\n{line}\n"
    ENV_FILE.write_text(text)
    print(f"  {DIM}.env updated{RESET}")


def run_migrations() -> bool:
    print("  running migrations…")
    result = subprocess.run(
        [sys.executable, str(BACKEND_DIR / "scripts" / "migrate.py")],
        cwd=BACKEND_DIR,
        capture_output=True,
        text=True,
    )
    output = (result.stdout + result.stderr).strip()
    for raw in output.splitlines():
        if raw.strip():
            print(f"    {raw}")
    return result.returncode == 0


def cmd_start() -> None:
    print(f"Starting dev Postgres in {DIM}{PGDATA}{RESET}")
    server = get_server()
    uri = server.get_uri()
    print(f"  {GREEN}running{RESET}")
    write_database_url(uri)
    if not run_migrations():
        sys.exit(f"{RED}Migrations failed.{RESET}")
    print(f"\n{GREEN}Ready.{RESET} Start the API with:")
    print(f"  {DIM}./venv/bin/python -m uvicorn app.main:app --reload{RESET}")


def cmd_stop() -> None:
    server = get_server(create=False)
    if server is None:
        print("Nothing to stop — no data directory.")
        return
    try:
        server.cleanup()
        print(f"{YELLOW}Stopped.{RESET} Data kept; `start` brings it back.")
    except Exception as exc:
        print(f"{RED}Could not stop cleanly: {exc}{RESET}")


def cmd_status() -> None:
    if not PGDATA.exists():
        print("Not initialised. Run: python scripts/dev_db.py start")
        return

    import asyncpg

    server = get_server()
    uri = server.get_uri()

    async def inspect() -> None:
        try:
            conn = await asyncpg.connect(uri, timeout=5)
        except Exception as exc:
            print(f"{RED}Not reachable: {exc}{RESET}")
            return
        try:
            version = (await conn.fetchval("SELECT version()")).split(",")[0]
            print(f"{GREEN}Running{RESET} — {version}")
            tables = await conn.fetch(
                "SELECT tablename FROM pg_tables WHERE schemaname='public' ORDER BY 1"
            )
            if not tables:
                print("  no tables — run: python scripts/migrate.py")
                return
            print(f"\n  {'table':22} rows")
            print(f"  {'─' * 30}")
            for row in tables:
                name = row["tablename"]
                count = await conn.fetchval(f'SELECT count(*) FROM "{name}"')
                print(f"  {name:22} {count}")
        finally:
            await conn.close()

    asyncio.run(inspect())


def cmd_reset() -> None:
    import shutil

    if input(f"{RED}Delete all local dev data?{RESET} type 'yes': ").strip() != "yes":
        sys.exit("Aborted.")
    server = get_server(create=False)
    if server is not None:
        try:
            server.cleanup()
        except Exception:
            pass
    shutil.rmtree(PGDATA, ignore_errors=True)
    print(f"{YELLOW}Deleted.{RESET} Recreating…\n")
    cmd_start()


COMMANDS = {"start": cmd_start, "stop": cmd_stop, "status": cmd_status, "reset": cmd_reset}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    parser.add_argument("command", choices=sorted(COMMANDS), nargs="?", default="start")
    COMMANDS[parser.parse_args().command]()
