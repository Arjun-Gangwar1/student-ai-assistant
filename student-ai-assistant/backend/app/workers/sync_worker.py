"""
Background worker using APScheduler.
Runs all polling jobs on a schedule:
  - Classroom sync:    every 2 hours
  - Calendar sync:     every 2 hours
  - Website scrape:    every 6 hours
  - Intelligence pipeline: after each sync
  - Deadline alerts:   every 30 minutes
  - Morning digest:    daily at configured time (IST)
"""

import logging
import asyncio
from datetime import datetime

import pytz
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from app.config import settings
from app.db.supabase import get_all_students
from app.connectors.classroom import sync_student_classroom
from app.connectors.calendar_conn import sync_student_calendar
from app.connectors.gmail_conn import sync_student_gmail
from app.connectors.website import sync_website_to_all_students
from app.intelligence.pipeline import process_student_items
from app.alerts.engine import run_deadline_alerts
from app.alerts.digest import send_all_digests

logger = logging.getLogger(__name__)
IST = pytz.timezone("Asia/Kolkata")


async def sync_all_gmail():
    students = await get_all_students()
    for student in students:
        try:
            new_count = await sync_student_gmail(student)
            if new_count > 0:
                await process_student_items(student)
        except Exception as e:
            logger.error(f"Gmail sync failed for {student['id']}: {e}")


async def sync_all_classrooms():
    students = await get_all_students()
    for student in students:
        try:
            await sync_student_classroom(student)
            await process_student_items(student)
        except Exception as e:
            logger.error(f"Classroom sync failed for {student['id']}: {e}")


async def sync_all_calendars():
    students = await get_all_students()
    for student in students:
        try:
            await sync_student_calendar(student)
        except Exception as e:
            logger.error(f"Calendar sync failed for {student['id']}: {e}")


async def scrape_website():
    try:
        await sync_website_to_all_students()
        students = await get_all_students()
        for student in students:
            await process_student_items(student)
    except Exception as e:
        logger.error(f"Website scrape failed: {e}")


def create_scheduler() -> AsyncIOScheduler:
    scheduler = AsyncIOScheduler(timezone=IST)

    # Gmail sync: every 3 minutes
    scheduler.add_job(
        sync_all_gmail,
        trigger=IntervalTrigger(minutes=3),
        id="gmail_sync",
        name="Gmail Sync",
        replace_existing=True,
        max_instances=1,
    )

    # Classroom + Calendar: every 2 hours
    scheduler.add_job(
        sync_all_classrooms,
        trigger=IntervalTrigger(minutes=settings.classroom_poll_interval_minutes),
        id="classroom_sync",
        name="Classroom Sync",
        replace_existing=True,
        max_instances=1,
    )
    scheduler.add_job(
        sync_all_calendars,
        trigger=IntervalTrigger(minutes=settings.calendar_poll_interval_minutes),
        id="calendar_sync",
        name="Calendar Sync",
        replace_existing=True,
        max_instances=1,
    )

    # Website scrape: every 6 hours
    scheduler.add_job(
        scrape_website,
        trigger=IntervalTrigger(minutes=settings.website_scrape_interval_minutes),
        id="website_scrape",
        name="Website Scrape",
        replace_existing=True,
        max_instances=1,
    )

    # Deadline alerts: every 30 minutes
    scheduler.add_job(
        run_deadline_alerts,
        trigger=IntervalTrigger(minutes=30),
        id="deadline_alerts",
        name="Deadline Alerts",
        replace_existing=True,
        max_instances=1,
    )

    # Morning digest: daily at configured time in IST
    digest_hour, digest_minute = map(int, settings.digest_send_time.split(":"))
    scheduler.add_job(
        send_all_digests,
        trigger=CronTrigger(hour=digest_hour, minute=digest_minute, timezone=IST),
        id="morning_digest",
        name="Morning Digest",
        replace_existing=True,
    )

    return scheduler


_scheduler: AsyncIOScheduler | None = None


def get_scheduler() -> AsyncIOScheduler:
    global _scheduler
    if _scheduler is None:
        _scheduler = create_scheduler()
    return _scheduler
