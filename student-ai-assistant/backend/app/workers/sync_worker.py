"""
Scheduled background jobs (APScheduler).

Reworked for the failure mode the previous schedule guaranteed: Gmail synced
every 3 minutes, looping every student serially, with the pipeline sleeping 2s
per item. One student with 50 new emails held the worker for over 100 seconds;
`max_instances=1` then dropped the next run silently rather than queueing it.
At two or three users the Gmail job would essentially never complete a full pass.

Now: a 30-minute Gmail interval (with incremental fetch, so a pass is cheap),
students processed with bounded concurrency, and misfire handling that
coalesces a backlog instead of stampeding.
"""

import asyncio
import logging
from datetime import datetime, timedelta

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from app.alerts.digest import send_due_digests
from app.alerts.engine import run_deadline_alerts
from app.config import settings
from app.connectors.calendar_conn import sync_student_calendar
from app.connectors.classroom import sync_student_classroom
from app.connectors.gmail_conn import sync_student_gmail
from app.connectors.website import sync_website_content
from app.db import queries
from app.intelligence.pipeline import process_student_items
from app.utils.date_utils import IST

logger = logging.getLogger(__name__)

# Google's per-user quotas are generous but the pipeline behind each sync is
# not; four students in flight keeps LLM concurrency near the Groq rate limit.
MAX_CONCURRENT_STUDENTS = 4


async def sync_one_student(student: dict) -> dict:
    """
    Full sync for one student: all connectors, then the intelligence pipeline.
    Each connector's failure is contained so one bad token cannot stop the rest.
    """
    student_id = str(student["id"])
    results: dict = {}

    for name, coroutine in (
        ("classroom", sync_student_classroom(student)),
        ("calendar", sync_student_calendar(student)),
        ("gmail", sync_student_gmail(student)),
    ):
        try:
            results[name] = await coroutine
        except Exception as exc:
            logger.error("%s sync failed for %s: %s", name, student_id, exc)
            results[name] = {"error": str(exc)}

    try:
        results["processed"] = await process_student_items(student)
    except Exception as exc:
        logger.error("Pipeline failed for %s: %s", student_id, exc)
        results["processed"] = {"error": str(exc)}

    return results


async def _for_each_student(job_name: str, handler) -> None:
    """Run `handler(student)` over all students with bounded concurrency."""
    try:
        students = await queries.get_active_students(with_google_tokens=True)
    except Exception as exc:
        logger.error("%s: could not load students: %s", job_name, exc)
        return

    if not students:
        return

    semaphore = asyncio.Semaphore(MAX_CONCURRENT_STUDENTS)

    async def guarded(student: dict):
        async with semaphore:
            try:
                return await handler(student)
            except Exception as exc:
                logger.error("%s failed for %s: %s", job_name, student["id"], exc)
                return None

    results = await asyncio.gather(*(guarded(s) for s in students))
    succeeded = sum(1 for r in results if r is not None)
    logger.info("%s: %d/%d students", job_name, succeeded, len(students))


# ─── Jobs ────────────────────────────────────────────────────────────────────

async def job_sync_gmail() -> None:
    async def handler(student: dict):
        result = await sync_student_gmail(student)
        if result.get("emails"):
            await process_student_items(student)
        return result

    await _for_each_student("gmail_sync", handler)


async def job_sync_classroom() -> None:
    async def handler(student: dict):
        result = await sync_student_classroom(student)
        await process_student_items(student)
        return result

    await _for_each_student("classroom_sync", handler)


async def job_sync_calendar() -> None:
    await _for_each_student("calendar_sync", sync_student_calendar)


async def job_scrape_website() -> None:
    """
    Scrape the college site once, then let each student's pipeline classify it.
    """
    try:
        count = await sync_website_content()
        logger.info("website_scrape: %d item(s) distributed", count)
    except Exception as exc:
        logger.error("website_scrape failed: %s", exc)
        return

    if count:
        await _for_each_student("website_pipeline", process_student_items)


async def job_purge_deleted() -> None:
    """Hard-delete accounts past their erasure grace period (DPDP)."""
    try:
        await queries.purge_deleted_students(grace_days=7)
    except Exception as exc:
        logger.error("purge_deleted failed: %s", exc)


def _soon(seconds: int) -> datetime:
    """
    First run time for an interval job.

    IntervalTrigger counts its first fire a full interval after startup, so a
    restart silently parks every sync for up to its whole period — two hours
    for Classroom. A deploy landing mid-pipeline therefore left items ingested
    but unprocessed, with nothing due to retry them for the rest of that window.
    Staggered here rather than all at zero so a cold boot does not fire every
    connector at once while the embedding model is still loading.
    """
    return datetime.now(IST) + timedelta(seconds=seconds)


def create_scheduler() -> AsyncIOScheduler:
    scheduler = AsyncIOScheduler(
        timezone=IST,
        job_defaults={
            # If a run is missed (deploy, restart, overrun), run once on return
            # rather than firing every missed interval back to back.
            "coalesce": True,
            "max_instances": 1,
            "misfire_grace_time": 300,
        },
    )

    scheduler.add_job(
        job_sync_gmail,
        IntervalTrigger(minutes=settings.gmail_poll_interval_minutes),
        next_run_time=_soon(90),
        id="gmail_sync", name="Gmail sync", replace_existing=True,
    )
    scheduler.add_job(
        job_sync_classroom,
        IntervalTrigger(minutes=settings.classroom_poll_interval_minutes),
        next_run_time=_soon(150),
        id="classroom_sync", name="Classroom sync", replace_existing=True,
    )
    scheduler.add_job(
        job_sync_calendar,
        IntervalTrigger(minutes=settings.calendar_poll_interval_minutes),
        next_run_time=_soon(210),
        id="calendar_sync", name="Calendar sync", replace_existing=True,
    )
    scheduler.add_job(
        job_scrape_website,
        IntervalTrigger(minutes=settings.website_scrape_interval_minutes),
        id="website_scrape", name="Website scrape", replace_existing=True,
    )
    scheduler.add_job(
        run_deadline_alerts,
        IntervalTrigger(minutes=15),
        id="deadline_alerts", name="Deadline alerts", replace_existing=True,
    )
    # Every 15 minutes rather than once daily: each student has their own
    # digest_time, and the job sends only to those whose time has just passed.
    scheduler.add_job(
        send_due_digests,
        IntervalTrigger(minutes=15),
        id="morning_digest", name="Morning digest", replace_existing=True,
    )
    scheduler.add_job(
        job_purge_deleted,
        CronTrigger(hour=3, minute=0, timezone=IST),
        id="purge_deleted", name="Purge deleted accounts", replace_existing=True,
    )

    return scheduler


_scheduler: AsyncIOScheduler | None = None


def get_scheduler() -> AsyncIOScheduler:
    global _scheduler
    if _scheduler is None:
        _scheduler = create_scheduler()
    return _scheduler
