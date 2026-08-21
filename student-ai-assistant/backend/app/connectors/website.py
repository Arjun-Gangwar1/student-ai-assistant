"""
College website scraper — IIT Dharwad portals.
Scrapes news items and academic calendar PDFs from public pages.
No OAuth required. Runs every 6 hours via cron.
"""

import logging
import hashlib
import re

import httpx
from bs4 import BeautifulSoup
from tenacity import retry, stop_after_attempt, wait_exponential

from app.db.supabase import upsert_item, get_all_students

logger = logging.getLogger(__name__)

IITDH_BASE        = "https://iitdh.ac.in"
IITDH_NEWS_URL    = "https://iitdh.ac.in/news"
IITDH_CALENDAR_URL = "https://iitdh.ac.in/academic-calendar"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (compatible; StudentAIAssistant/1.0; "
        "research project at IIT Dharwad)"
    )
}

# IIT Dharwad Drupal 10 site structure (verified 2026-06):
#   News listing page (/news):
#     <div class="views-row">
#       <div class="field_publishing_date">DD Mon YYYY</div>
#       <div class="title"><a href="/...">Title</a></div>
#     </div>
#
#   Academic calendar page (/academic-calendar):
#     PDF links with "Academic calendar" in the link text


def _content_hash(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()[:16]


def _abs_url(href: str) -> str:
    if href.startswith("http"):
        return href
    return f"{IITDH_BASE}{href}"


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=5, max=30))
async def fetch_iitdh_news() -> list[dict]:
    """Scrape IIT Dharwad /news page using verified Drupal views-row structure."""
    news = []
    async with httpx.AsyncClient(headers=HEADERS, timeout=15, follow_redirects=True) as client:
        try:
            resp = await client.get(IITDH_NEWS_URL)
            resp.raise_for_status()
        except Exception as e:
            logger.error(f"Failed to fetch IITdh news: {e}")
            return []

        soup = BeautifulSoup(resp.text, "lxml")

        for row in soup.find_all("div", class_="views-row"):
            # Date: <div class="field_publishing_date">
            date_el = row.find("div", class_="field_publishing_date")
            date_str = date_el.get_text(strip=True) if date_el else ""

            # Title link: <div class="title"><a href="...">
            title_el = row.find("div", class_="title")
            if not title_el:
                continue
            a = title_el.find("a", href=True)
            if not a:
                continue
            title = a.get_text(strip=True)
            link  = _abs_url(a["href"])

            if not title:
                continue

            news.append({
                "title":    title,
                "link":     link,
                "date_str": date_str,
                "raw":      f"IIT Dharwad News: {title}\nDate: {date_str}\nURL: {link}",
                "source_id": _content_hash(title + link),
                "portal":   "iitdh_news",
            })

    logger.info(f"Scraped {len(news)} news items from IITdh")
    return news


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=5, max=30))
async def fetch_iitdh_calendar() -> list[dict]:
    """Scrape IIT Dharwad /academic-calendar page for PDF calendar links."""
    items = []
    async with httpx.AsyncClient(headers=HEADERS, timeout=15, follow_redirects=True) as client:
        try:
            resp = await client.get(IITDH_CALENDAR_URL)
            resp.raise_for_status()
        except Exception as e:
            logger.error(f"Failed to fetch IITdh academic calendar: {e}")
            return []

        soup = BeautifulSoup(resp.text, "lxml")

        for a in soup.find_all("a", href=True):
            href  = a.get("href", "")
            text  = a.get_text(strip=True)
            # Match "Academic calendar" links with PDF/docx content
            if not text or "calendar" not in text.lower():
                continue
            if not any(ext in href.lower() for ext in [".pdf", ".docx", ".doc"]):
                continue

            link  = _abs_url(href)
            # Extract semester/year hint from link text
            items.append({
                "title":    f"Academic Calendar: {text}",
                "link":     link,
                "date_str": "",
                "raw":      f"IIT Dharwad Academic Calendar: {text}\nPDF: {link}",
                "source_id": _content_hash(text + link),
                "portal":   "iitdh_academic_calendar",
            })

    logger.info(f"Scraped {len(items)} academic calendar items from IITdh")
    return items


async def fetch_all_iitdh_content() -> list[dict]:
    """Fetch both news and academic calendar items."""
    news     = await fetch_iitdh_news()
    calendar = await fetch_iitdh_calendar()
    return news + calendar


async def sync_website_to_all_students() -> int:
    """Broadcast new website content to all registered students."""
    content = await fetch_all_iitdh_content()
    if not content:
        logger.warning("No IITdh content scraped — check selectors or network")
        return 0

    students = await get_all_students()
    count = 0

    for student in students:
        for item in content:
            await upsert_item({
                "student_id": student["id"],
                "source":     "website",
                "source_id":  item["source_id"],
                "raw_content": item["raw"],
                "title":      item["title"],
                "metadata": {
                    "link":     item["link"],
                    "date_str": item["date_str"],
                    "portal":   item["portal"],
                },
            })
            count += 1

    logger.info(f"Synced {len(content)} IITdh items to {len(students)} students ({count} upserts total)")
    return count
