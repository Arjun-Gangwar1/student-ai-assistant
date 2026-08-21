"""
Re-sync all students' Gmail into the new structured emails table.
Also runs the intelligence pipeline to (re-)embed the items.

Usage:
    cd backend
    source venv/bin/activate
    python scripts/run_gmail_sync.py
"""

import asyncio
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
logger = logging.getLogger(__name__)


async def main():
    from app.db.supabase import get_all_students
    from app.connectors.gmail_conn import sync_student_gmail
    from app.intelligence.pipeline import process_student_items

    students = await get_all_students()
    logger.info(f"Found {len(students)} students")

    for student in students:
        email = student.get("email", student["id"])
        tokens = student.get("google_tokens") or {}
        scopes = tokens.get("scopes", [])
        has_gmail = any("gmail" in s for s in scopes)

        if not has_gmail:
            logger.info(f"Skipping {email} — no Gmail scope")
            continue

        logger.info(f"\nSyncing Gmail for {email}...")
        count = await sync_student_gmail(student, max_emails=50)
        logger.info(f"  Fetched: {count} emails into structured tables")

        logger.info(f"  Running AI pipeline for {email}...")
        processed = await process_student_items(student)
        logger.info(f"  Pipeline processed: {processed} items")

    logger.info("\nGmail sync complete.")


if __name__ == "__main__":
    asyncio.run(main())
