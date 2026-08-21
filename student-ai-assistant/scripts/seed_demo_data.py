#!/usr/bin/env python3
"""
Seed realistic demo data — for local development and demos.

Creates one student with deadlines, notices and email spanning every category,
each with a real embedding so retrieval and the digest behave as they would with
live data.

    python scripts/seed_demo_data.py [--reset]
"""

import argparse
import asyncio
import sys
from datetime import timedelta
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent / "backend"
sys.path.insert(0, str(BACKEND_DIR))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(BACKEND_DIR / ".env")

from app.db import queries  # noqa: E402
from app.db.pool import acquire, close_pool, init_pool  # noqa: E402
from app.intelligence.embedder import embed_batch  # noqa: E402
from app.utils.date_utils import now_utc  # noqa: E402

DEMO_EMAIL = "demo@iitdh.ac.in"

# (source, source_id, title, body, category, priority, days_until_due)
CONTENT = [
    ("classroom", "cw_001", "MA201 Linear Algebra: Assignment 3",
     "Submit Assignment 3 covering eigenvalues, eigenvectors and diagonalization "
     "through Google Classroom. Late submissions lose 20% per day.",
     "academic", "HIGH", 2),
    ("classroom", "cw_002", "PH103 Engineering Physics: Quiz 2",
     "Quiz 2 covers thermodynamics, entropy and the second law. Bring your calculator.",
     "academic", "HIGH", 1),
    ("classroom", "cw_003", "CS201 Data Structures: Lab 6 report",
     "Submit the Lab 6 report on balanced binary search trees, including complexity analysis.",
     "academic", "MEDIUM", 5),
    ("classroom", "ann_001", "MA201: Tutorial rescheduled",
     "This week's tutorial moves to Thursday 3pm in LH-2 because of the department seminar.",
     "academic", "MEDIUM", None),
    ("gmail", "msg_001", "Qualcomm campus drive — registration closes Friday",
     "Registration for the Qualcomm campus placement drive closes Friday 6pm. "
     "Eligibility: CS, EE and MA branches with CGPA above 7.0. Register on the placement portal.",
     "placement", "HIGH", 4),
    ("gmail", "msg_002", "Semester fee payment reminder",
     "The fee for the odd semester must be paid before the end of the month. "
     "Pay through the SBI Collect portal and keep the receipt.",
     "admin", "HIGH", 8),
    ("gmail", "msg_003", "Hostel room allocation for next semester",
     "Room allocation forms for the next semester are open. Submit to the warden office.",
     "hostel", "MEDIUM", 12),
    ("website", "web_001", "Mess menu — week of 25 August",
     "Monday: poha, tea. Tuesday: idli sambar, chutney. Wednesday: aloo paratha, curd. "
     "Thursday: upma. Friday: dosa. Weekend special: chole bhature.",
     "mess", "LOW", None),
    ("website", "web_002", "Institute holiday — Ganesh Chaturthi",
     "The institute will remain closed for Ganesh Chaturthi. Classes resume the following day.",
     "admin", "LOW", None),
    ("website", "web_003", "Inter-IIT Tech Meet selection trials",
     "Selection trials for the Inter-IIT Tech Meet contingent are open to all years. "
     "Register with the Technical Secretary.",
     "event", "MEDIUM", 6),
]


async def seed(reset: bool) -> None:
    await init_pool()
    try:
        async with acquire() as conn:
            if reset:
                await conn.execute("DELETE FROM students WHERE email = $1", DEMO_EMAIL)
                print("Removed existing demo student")

        student = await queries.upsert_student(
            google_id="demo_google_id_001",
            email=DEMO_EMAIL,
            name="Demo Student",
            scopes=[
                "https://www.googleapis.com/auth/classroom.courses.readonly",
                "https://www.googleapis.com/auth/calendar.events.readonly",
                "https://www.googleapis.com/auth/gmail.readonly",
            ],
            consent_version="2026-08-21",
        )
        student_id = str(student["id"])
        await queries.update_student_profile(student_id, year=2, branch="CS")
        await queries.set_gmail_enabled(student_id, True)
        print(f"Demo student: {student_id}")

        print("Embedding demo content…")
        vectors = await embed_batch([f"{t}\n{b}" for _, _, t, b, _, _, _ in CONTENT])

        now = now_utc()
        deadline_count = 0
        for (source, source_id, title, body, category, priority, days), vector in zip(
            CONTENT, vectors
        ):
            due_at = now + timedelta(days=days, hours=6) if days is not None else None

            item = await queries.upsert_item(
                student_id=student_id,
                source=source,
                source_id=source_id,
                raw_content=body,
                title=title,
                deadline=due_at,
                metadata={"demo": True},
            )
            await queries.save_item_analysis(
                item_id=str(item["id"]),
                category=category,
                priority=priority,
                relevance_score=0.9 if priority == "HIGH" else 0.6,
                summary=title,
                embedding=vector,
            )
            if due_at:
                await queries.upsert_deadline(
                    student_id=student_id,
                    dedup_key=f"{source}:{source_id}",
                    item_id=str(item["id"]),
                    title=title,
                    due_at=due_at,
                    source=source,
                    confirmed=source in ("classroom", "calendar"),
                    confidence=1.0 if source == "classroom" else 0.85,
                )
                deadline_count += 1

        print(f"Seeded {len(CONTENT)} items and {deadline_count} deadlines.")
        print(f"\nSign in as {DEMO_EMAIL} is not possible (no real Google account);")
        print("use this data through scripts, or link a real account and re-run.")
    finally:
        await close_pool()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--reset", action="store_true", help="delete the demo student first")
    asyncio.run(seed(parser.parse_args().reset))
