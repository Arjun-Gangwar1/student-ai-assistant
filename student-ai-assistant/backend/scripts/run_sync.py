"""
One-shot script to trigger Classroom + Calendar sync for all students.
Run from backend/ with: python scripts/run_sync.py
"""
import asyncio
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.db.supabase import get_all_students
from app.connectors.classroom import sync_student_classroom
from app.connectors.calendar_conn import sync_student_calendar
from app.intelligence.pipeline import process_student_items


async def main():
    students = await get_all_students()
    if not students:
        print("No students found. Login first, then re-run.")
        return

    for student in students:
        print(f"\n--- Syncing: {student.get('email')} ---")

        print("  Classroom...", end=" ", flush=True)
        try:
            result = await sync_student_classroom(student)
            print(f"✓  {result}")
        except Exception as e:
            print(f"✗  {e}")

        print("  Calendar...", end=" ", flush=True)
        try:
            count = await sync_student_calendar(student)
            print(f"✓  {count} events")
        except Exception as e:
            print(f"✗  {e}")

        print("  AI pipeline (classify + embed)...", end=" ", flush=True)
        try:
            processed = await process_student_items(student)
            print(f"✓  {processed} items processed")
        except Exception as e:
            print(f"✗  {e}")

    print("\nSync complete. Refresh your dashboard.")


if __name__ == "__main__":
    asyncio.run(main())
