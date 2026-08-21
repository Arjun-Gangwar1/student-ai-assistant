"""
Seeds demo data for local development and hackathon demos.
Creates 1 demo student + realistic deadlines + items.
Usage: python scripts/seed_demo_data.py
"""

import sys
import asyncio
from pathlib import Path
from datetime import datetime, timezone, timedelta

sys.path.insert(0, str(Path(__file__).parent.parent / "backend"))

from dotenv import load_dotenv
load_dotenv(Path(__file__).parent.parent / "backend" / ".env")

from app.db.supabase import get_supabase

DEMO_STUDENT = {
    "google_id": "demo_google_id_001",
    "email": "demo@iitdh.ac.in",
    "name": "Demo Student",
    "year": 2,
    "branch": "CS",
    "telegram_chat_id": None,
}

now = datetime.now(timezone.utc)

DEMO_ITEMS = [
    {
        "source": "classroom",
        "source_id": "cls_001",
        "raw_content": "MA201 Linear Algebra: Assignment 3 on Eigenvalues. Submit by 18 Feb 23:59.",
        "title": "MA201: Assignment 3 — Eigenvalues",
        "category": "academic",
        "priority": "HIGH",
        "relevance_score": 0.95,
        "summary": "Linear Algebra Assignment 3 on Eigenvalues due Feb 18",
        "deadline": (now + timedelta(days=1, hours=6)).isoformat(),
    },
    {
        "source": "classroom",
        "source_id": "cls_002",
        "raw_content": "CS301 Algorithms: Mid-semester exam on Feb 22. Covers topics till unit 4.",
        "title": "CS301: Mid-Semester Exam",
        "category": "academic",
        "priority": "HIGH",
        "relevance_score": 1.0,
        "summary": "Algorithms mid-sem exam on Feb 22, covers Unit 1–4",
        "deadline": (now + timedelta(days=5)).isoformat(),
    },
    {
        "source": "calendar",
        "source_id": "cal_001",
        "raw_content": "Hackathon registration closes: TechFest 2026 IIT Dharwad",
        "title": "TechFest 2026: Registration closes",
        "category": "event",
        "priority": "MEDIUM",
        "relevance_score": 0.8,
        "summary": "TechFest hackathon registration deadline",
        "deadline": (now + timedelta(days=3)).isoformat(),
    },
    {
        "source": "website",
        "source_id": "web_001",
        "raw_content": "NOTICE: Mess menu for this week. Monday: Rajma Chawal. Tuesday: Chole Bhature. Wednesday: Dal Makhani. Thursday: Biryani. Friday: Pizza.",
        "title": "Weekly Mess Menu",
        "category": "mess",
        "priority": "LOW",
        "relevance_score": 0.7,
        "summary": "This week's mess menu: Mon Rajma, Tue Chole, Wed Dal Makhani, Thu Biryani, Fri Pizza",
        "deadline": None,
    },
    {
        "source": "website",
        "source_id": "web_002",
        "raw_content": "ACADEMIC NOTICE: Semester fee payment last date is Feb 28, 2026. Late fee of Rs 500 per day after due date.",
        "title": "Semester Fee Payment Deadline",
        "category": "admin",
        "priority": "HIGH",
        "relevance_score": 1.0,
        "summary": "Semester fee payment due Feb 28 — late fee Rs 500/day after",
        "deadline": (now + timedelta(days=10)).isoformat(),
    },
]

DEMO_DEADLINES = [
    {
        "title": "MA201: Assignment 3 — Eigenvalues",
        "due_at": (now + timedelta(days=1, hours=6)).isoformat(),
        "source": "classroom",
        "confirmed": True,
    },
    {
        "title": "CS301: Mid-Semester Exam",
        "due_at": (now + timedelta(days=5)).isoformat(),
        "source": "classroom",
        "confirmed": True,
    },
    {
        "title": "TechFest 2026: Registration closes",
        "due_at": (now + timedelta(days=3)).isoformat(),
        "source": "calendar",
        "confirmed": True,
    },
    {
        "title": "Semester Fee Payment",
        "due_at": (now + timedelta(days=10)).isoformat(),
        "source": "website",
        "confirmed": False,  # AI extracted — not yet confirmed
    },
]


def seed():
    db = get_supabase()
    print("Seeding demo data...")

    # Upsert student
    res = db.table("students").upsert(DEMO_STUDENT, on_conflict="google_id").execute()
    student_id = res.data[0]["id"]
    print(f"  Student: {student_id}")

    # Upsert items
    for item in DEMO_ITEMS:
        item["student_id"] = student_id
        db.table("items").upsert(item, on_conflict="student_id,source,source_id").execute()
    print(f"  Items: {len(DEMO_ITEMS)} seeded")

    # Upsert deadlines
    for dl in DEMO_DEADLINES:
        dl["student_id"] = student_id
        db.table("deadlines").insert(dl).execute()
    print(f"  Deadlines: {len(DEMO_DEADLINES)} seeded")

    print(f"\nDemo student ID: {student_id}")
    print("Update STUDENT_ID in frontend pages to this value for testing.")


if __name__ == "__main__":
    seed()
