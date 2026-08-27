#!/usr/bin/env python3
"""
Re-queue items whose "classification" is really a placeholder.

Before the `degraded` marker existed, a failed classification call returned the
same FALLBACK values as a genuine "general" verdict, and the pipeline stamped
processed_at on it regardless. Nothing rescans processed rows, so a day of 429s
froze placeholders in permanently -- 325 of 351 items on the first production
corpus. Clearing processed_at puts them back in the queue.

Identified by the full FALLBACK signature rather than category alone:

    category = 'general' AND priority = 'LOW'
    AND relevance_score ~= 0.4        -- a real column, so compared with tolerance
    AND summary = title               -- the pipeline's `summary or title` substitution

A genuine general/LOW/0.4 item whose summary happens to equal its title would
also be re-queued. That costs one reclassification and cannot corrupt anything,
which is the right side to err on.

Usage:
    DATABASE_URL=... python scripts/requeue_degraded.py           # dry run
    DATABASE_URL=... python scripts/requeue_degraded.py --apply
"""

import asyncio
import os
import sys

import asyncpg

PREDICATE = """
    processed_at IS NOT NULL
    AND category = 'general'
    AND priority = 'LOW'
    AND abs(relevance_score - 0.4) < 0.001
    AND summary = title
"""


async def main() -> int:
    apply = "--apply" in sys.argv
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        print("DATABASE_URL is not set", file=sys.stderr)
        return 2

    conn = await asyncpg.connect(dsn, statement_cache_size=0)
    try:
        total = await conn.fetchval("select count(*) from items where processed_at is not null")
        affected = await conn.fetchval(f"select count(*) from items where {PREDICATE}")

        print(f"processed items      : {total}")
        print(f"placeholder rows     : {affected}"
              f"{f'  ({affected / total:.0%})' if total else ''}")
        print(f"genuine classifications kept: {total - affected}")

        if not affected:
            print("\nnothing to re-queue")
            return 0

        print("\nsample:")
        for row in await conn.fetch(
            f"select source, left(coalesce(title,''),64) t from items where {PREDICATE} limit 5"
        ):
            print(f"  [{row['source']}] {row['t']}")

        if not apply:
            print("\nDRY RUN -- re-run with --apply to clear processed_at on these rows")
            return 0

        updated = await conn.execute(
            f"update items set processed_at = NULL, updated_at = now() where {PREDICATE}"
        )
        print(f"\n{updated}")
        remaining = await conn.fetchval("select count(*) from items where processed_at is null")
        print(f"items now queued for reclassification: {remaining}")
        return 0
    finally:
        await conn.close()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
