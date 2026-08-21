"""
IIT Dharwad website scraper.

This is the moat. Classroom and Calendar are generic — any assistant can read
them. Per-college circular parsing is what a horizontal product will not build,
and it is the reason a student at this campus would choose this over a general
tool.

Scaling note: `sync_website_content` writes one `items` row per notice per
student, which is what the schema's per-student model requires today. At 1,000
students × 50 notices that is 50,000 rows and 50,000 classification calls for
50 distinct pieces of content. The fix is a shared `global_items` table with
per-student relevance joins; it is deliberately deferred until the retention
gate is passed, and the cost is bounded here by only distributing *recent*
notices. See docs/ROADMAP.md.
"""

import hashlib
import logging
import re
from datetime import datetime, timedelta

import httpx
from bs4 import BeautifulSoup
from tenacity import retry, stop_after_attempt, wait_exponential

from app.db import queries
from app.utils.date_utils import IST, now_ist

logger = logging.getLogger(__name__)

BASE_URL = "https://iitdh.ac.in"
NEWS_URL = f"{BASE_URL}/news"
CALENDAR_URL = f"{BASE_URL}/academic-calendar"
ANNOUNCEMENTS_URL = f"{BASE_URL}/announcements"

# Identifies the crawler honestly and gives the college someone to contact.
HEADERS = {
    "User-Agent": (
        "StudentAIAssistant/1.0 (student project, IIT Dharwad; "
        "contact: is24bm014@iitdh.ac.in)"
    )
}

REQUEST_TIMEOUT = 20
# Only notices from the last 60 days are distributed. An archive page going back
# years would otherwise be ingested and classified in full on first run.
MAX_NOTICE_AGE_DAYS = 60

DATE_FORMATS = ("%d %b %Y", "%d %B %Y", "%d-%m-%Y", "%Y-%m-%d", "%d/%m/%Y")


def _content_hash(*parts: str) -> str:
    return hashlib.sha256("|".join(parts).encode()).hexdigest()[:16]


def _absolute(href: str) -> str:
    if href.startswith(("http://", "https://")):
        return href
    return f"{BASE_URL}/{href.lstrip('/')}"


def _parse_notice_date(text: str) -> datetime | None:
    cleaned = re.sub(r"\s+", " ", (text or "").strip())
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(cleaned, fmt).replace(tzinfo=IST)
        except ValueError:
            continue
    return None


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=2, min=4, max=30))
async def _fetch(url: str) -> str | None:
    async with httpx.AsyncClient(
        headers=HEADERS, timeout=REQUEST_TIMEOUT, follow_redirects=True
    ) as client:
        response = await client.get(url)
        response.raise_for_status()
        return response.text


async def fetch_news() -> list[dict]:
    """
    Scrape the news listing.

    The site is Drupal 10 and renders listings as `div.views-row`. Selectors are
    tried in order and the parser reports when none match, because a silent
    empty result looks identical to "no news this week" — and this scraper being
    quietly dead is exactly the failure that would go unnoticed for months.
    """
    try:
        html = await _fetch(NEWS_URL)
    except Exception as exc:
        logger.error("News fetch failed: %s", exc)
        return []
    if not html:
        return []

    soup = BeautifulSoup(html, "lxml")
    rows = soup.select("div.views-row") or soup.select("div.view-content div.item")
    if not rows:
        logger.warning(
            "No news rows matched at %s — the site layout has probably changed. "
            "Re-check the selectors.", NEWS_URL
        )
        return []

    cutoff = now_ist() - timedelta(days=MAX_NOTICE_AGE_DAYS)
    notices: list[dict] = []

    for row in rows:
        link_el = row.select_one("div.title a[href]") or row.select_one("a[href]")
        if not link_el:
            continue

        title = link_el.get_text(strip=True)
        if not title:
            continue

        date_el = row.select_one("div.field_publishing_date, .date, time")
        date_text = date_el.get_text(strip=True) if date_el else ""
        published = _parse_notice_date(date_text)

        if published and published < cutoff:
            continue

        link = _absolute(link_el["href"])
        notices.append({
            "title": title,
            "link": link,
            "date_text": date_text,
            "published": published,
            "raw": f"IIT Dharwad notice: {title}\nPublished: {date_text or 'unknown'}\nLink: {link}",
            "source_id": _content_hash(title, link),
            "portal": "iitdh_news",
        })

    logger.info("Scraped %d news item(s)", len(notices))
    return notices


async def fetch_academic_calendar() -> list[dict]:
    """Scrape links to academic-calendar documents."""
    try:
        html = await _fetch(CALENDAR_URL)
    except Exception as exc:
        logger.error("Academic calendar fetch failed: %s", exc)
        return []
    if not html:
        return []

    soup = BeautifulSoup(html, "lxml")
    items: list[dict] = []

    for anchor in soup.select("a[href]"):
        href = anchor.get("href", "")
        text = anchor.get_text(strip=True)
        if not text or "calendar" not in text.lower():
            continue
        if not href.lower().endswith((".pdf", ".docx", ".doc")):
            continue

        link = _absolute(href)
        items.append({
            "title": f"Academic Calendar: {text}",
            "link": link,
            "date_text": "",
            "published": None,
            "raw": f"IIT Dharwad academic calendar: {text}\nDocument: {link}",
            "source_id": _content_hash(text, link),
            "portal": "iitdh_academic_calendar",
        })

    logger.info("Scraped %d academic calendar link(s)", len(items))
    return items


async def fetch_all_content() -> list[dict]:
    news = await fetch_news()
    calendar = await fetch_academic_calendar()

    # Dedup across pages — a notice often appears on both.
    seen: set[str] = set()
    combined: list[dict] = []
    for item in news + calendar:
        if item["source_id"] not in seen:
            seen.add(item["source_id"])
            combined.append(item)
    return combined


async def sync_website_content() -> int:
    """
    Scrape once and distribute to every student. Returns the notice count.
    """
    content = await fetch_all_content()
    if not content:
        logger.warning("No website content scraped — check selectors or connectivity")
        return 0

    students = await queries.get_active_students()
    if not students:
        return 0

    written = 0
    for student in students:
        student_id = str(student["id"])
        for item in content:
            try:
                await queries.upsert_item(
                    student_id=student_id,
                    source="website",
                    source_id=item["source_id"],
                    raw_content=item["raw"],
                    title=item["title"],
                    metadata={
                        "link": item["link"],
                        "date_text": item["date_text"],
                        "portal": item["portal"],
                    },
                )
                written += 1
            except Exception as exc:
                logger.error("Website item upsert failed for %s: %s", student_id, exc)

    logger.info(
        "Website sync: %d notice(s) → %d student(s) (%d upserts)",
        len(content), len(students), written,
    )
    return len(content)
