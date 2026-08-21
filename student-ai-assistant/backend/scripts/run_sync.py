#!/usr/bin/env python3
"""
One-shot sync for every student — Classroom, Calendar, Gmail, then the pipeline.

    python scripts/run_sync.py            # all students
    python scripts/run_sync.py --email a@iitdh.ac.in
"""

import argparse
import asyncio
import logging
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_DIR))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(BACKEND_DIR / ".env")

from app.db import queries  # noqa: E402
from app.db.pool import close_pool, init_pool  # noqa: E402
from app.workers.sync_worker import sync_one_student  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--email", help="sync only this student")
    args = parser.parse_args()

    await init_pool()
    try:
        students = await queries.get_active_students(with_google_tokens=True)
        if args.email:
            students = [s for s in students if s["email"].lower() == args.email.lower()]

        if not students:
            print("No matching students. Sign in through the web app first.")
            return

        for student in students:
            print(f"\n─── {student['email']} ───")
            if not student.get("google_tokens"):
                print("  no Google tokens — skipped")
                continue

            results = await sync_one_student(student)
            for source, result in results.items():
                print(f"  {source:12} {result}")
    finally:
        await close_pool()


if __name__ == "__main__":
    asyncio.run(main())
