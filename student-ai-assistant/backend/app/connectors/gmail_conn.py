"""
Gmail connector — structured email sync with attachment extraction.

Stores every email in two places:
  1. `emails` table  — fully structured (sender, date, body, attachments)
  2. `items`  table  — RAG layer (clean text, embedding, category)

Attachment text (PDF, docx, txt, md) also gets its own `items` row
so the bot can find "the PDF Arjun sent about Assignment 3".

Scope required: https://www.googleapis.com/auth/gmail.readonly
"""

import base64
import io
import logging
import re
from datetime import datetime, timezone, timedelta

from bs4 import BeautifulSoup
from googleapiclient.discovery import build
from google.oauth2.credentials import Credentials
from tenacity import retry, stop_after_attempt, wait_exponential

from app.db.supabase import upsert_item, upsert_email, upsert_email_attachment

logger = logging.getLogger(__name__)

GMAIL_SCOPES    = ["https://www.googleapis.com/auth/gmail.readonly"]
LOOKBACK_DAYS   = 30
MAX_EMAILS      = 50
MAX_ATTACH_BYTES = 5 * 1024 * 1024   # 5 MB per attachment

# Mime types we can extract text from
TEXT_MIME_TYPES = {
    "application/pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",  # docx
    "application/msword",            # doc (best-effort)
    "text/plain",
    "text/markdown",
    "text/csv",
}
IMAGE_MIME_PREFIX = "image/"

# Links that look like shared documents / academic resources
DOC_LINK_RE = re.compile(
    r"https?://(?:docs\.google\.com|drive\.google\.com|github\.com|"
    r"classroom\.google\.com|forms\.gle|bit\.ly|[a-z0-9-]+\.iitdh\.ac\.in)[^\s\"'>)]*",
    re.I,
)

SENDER_RE = re.compile(r'^(?:"?([^"<]+)"?\s*)?<([^>]+)>$')


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _parse_sender(raw: str) -> tuple[str, str]:
    """'Arjun <a@b.com>' → ('Arjun', 'a@b.com')"""
    raw = raw.strip()
    m = SENDER_RE.match(raw)
    if m:
        name  = (m.group(1) or "").strip().strip('"')
        email = (m.group(2) or "").strip().lower()
        return name, email
    # plain email address with no angle brackets
    if "@" in raw:
        return raw.split("@")[0].replace(".", " ").title(), raw.lower()
    return raw, ""


def _get_header(headers: list, name: str) -> str:
    for h in headers:
        if h["name"].lower() == name.lower():
            return h["value"]
    return ""


def _extract_text_from_html(html: str) -> str:
    """Strip HTML tags and decode entities cleanly, preserving word spacing."""
    soup = BeautifulSoup(html, "lxml")
    for tag in soup(["script", "style", "head", "meta", "link", "img"]):
        tag.decompose()
    # separator=" " ensures adjacent text nodes are separated (prevents word merging)
    text = soup.get_text(separator=" ", strip=True)
    # Collapse multiple spaces → one, but keep single newlines
    text = re.sub(r" {2,}", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _strip_quoted_replies(text: str) -> str:
    """Remove classic > quoted lines and 'On ... wrote:' trailers."""
    lines = text.splitlines()
    clean = []
    for line in lines:
        stripped = line.strip()
        if stripped.startswith(">"):
            break
        if re.match(r"^On .{10,80} wrote:$", stripped):
            break
        clean.append(line)
    return "\n".join(clean).strip()


def _decode_part_data(data: str) -> str:
    """Base64url-decode a Gmail body part, always returning str."""
    try:
        # Gmail uses URL-safe base64; strip whitespace before decoding
        raw = base64.urlsafe_b64decode(data.replace(" ", "").replace("\n", "") + "==")
        return raw.decode("utf-8", errors="replace")
    except Exception:
        return ""


def _extract_body_parts(payload: dict) -> tuple[str, str]:
    """
    Recursively extract (plain_text, html_text) from a Gmail payload.
    Handles 'text/plain; charset=utf-8' style MIME types correctly.
    """
    mime = payload.get("mimeType", "")

    # Use startswith to handle "text/plain; charset=utf-8" etc.
    if mime.startswith("text/plain"):
        data = payload.get("body", {}).get("data", "")
        if data:
            return _decode_part_data(data), ""

    if mime.startswith("text/html"):
        data = payload.get("body", {}).get("data", "")
        if data:
            return "", _decode_part_data(data)

    if mime.startswith("multipart"):
        plain_parts, html_parts = [], []
        for part in payload.get("parts", []):
            p, h = _extract_body_parts(part)
            if p: plain_parts.append(p)
            if h: html_parts.append(h)
        return "\n".join(plain_parts), "\n".join(html_parts)

    return "", ""


def _clean_body(payload: dict, snippet: str) -> str:
    """Return the cleanest possible text body from a Gmail message payload."""
    plain, html = _extract_body_parts(payload)

    plain_stripped = plain.strip()
    # Ignore garbled plain text (bytes repr from broken email generators)
    plain_ok = plain_stripped and not plain_stripped.startswith(("b'", 'b"'))

    if plain_ok:
        text = _strip_quoted_replies(plain)
    elif html.strip():
        text = _strip_quoted_replies(_extract_text_from_html(html))
    else:
        text = snippet  # last resort: Gmail snippet

    # Convert unicode non-standard spaces (figure space, nbsp, etc.) → regular space
    text = re.sub(r"[   -    　]", " ", text)
    # Remove zero-width / invisible / directional control chars
    text = re.sub(r"[­​-‏‪-‮﻿͏⁠]", "", text)
    # Collapse runs of spaces, clean up newlines
    text = re.sub(r" {2,}", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()[:4000]


def _extract_doc_links(body: str) -> list[str]:
    """Find academic/document links mentioned in the email body."""
    return list(dict.fromkeys(DOC_LINK_RE.findall(body)))  # dedup, preserve order


def _collect_attachment_parts(payload: dict, parts_out: list) -> None:
    """Recursively find all attachment parts in a Gmail payload."""
    mime = payload.get("mimeType", "")
    filename = payload.get("filename", "")
    attach_id = payload.get("body", {}).get("attachmentId")

    if filename and attach_id:
        parts_out.append({
            "filename":     filename,
            "mime_type":    mime,
            "size_bytes":   payload.get("body", {}).get("size", 0),
            "attachment_id": attach_id,
        })

    for part in payload.get("parts", []):
        _collect_attachment_parts(part, parts_out)


def _extract_text_from_bytes(data: bytes, mime_type: str, filename: str) -> str:
    """Extract text from attachment bytes based on mime type."""
    try:
        if mime_type == "application/pdf" or filename.lower().endswith(".pdf"):
            import pdfplumber
            with pdfplumber.open(io.BytesIO(data)) as pdf:
                pages = [p.extract_text() or "" for p in pdf.pages[:20]]
            return "\n".join(pages).strip()[:6000]

        if (mime_type in (
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                "application/msword",
            ) or filename.lower().endswith((".docx", ".doc"))):
            from docx import Document
            doc = Document(io.BytesIO(data))
            return "\n".join(p.text for p in doc.paragraphs if p.text.strip())[:6000]

        if mime_type in ("text/plain", "text/markdown", "text/csv") or \
                filename.lower().endswith((".txt", ".md", ".csv")):
            return data.decode("utf-8", errors="ignore")[:6000]

    except Exception as e:
        logger.warning(f"Attachment text extraction failed ({filename}): {e}")

    return ""


def build_service(token_data: dict):
    creds = Credentials(
        token=token_data.get("access_token"),
        refresh_token=token_data.get("refresh_token"),
        token_uri="https://oauth2.googleapis.com/token",
        client_id=token_data.get("client_id"),
        client_secret=token_data.get("client_secret"),
        scopes=token_data.get("scopes", []) + GMAIL_SCOPES,
    )
    return build("gmail", "v1", credentials=creds, cache_discovery=False)


# ─── Main sync ────────────────────────────────────────────────────────────────

@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10))
async def sync_student_gmail(student: dict, max_emails: int = MAX_EMAILS) -> int:
    tokens = student.get("google_tokens")
    if not tokens:
        return 0

    stored_scopes = tokens.get("scopes", [])
    if not any("gmail" in s for s in stored_scopes):
        logger.info(f"Gmail scope missing for {student['id']} — skipping")
        return 0

    try:
        service = build_service(tokens)
    except Exception as e:
        logger.error(f"Gmail service build failed for {student['id']}: {e}")
        return 0

    student_id = student["id"]
    count = 0

    after_ts = int(
        (datetime.now(timezone.utc) - timedelta(days=LOOKBACK_DAYS)).timestamp()
    )
    query = (
        f"after:{after_ts} "
        "-category:promotions -category:social "
        "-in:spam -in:trash"
    )

    try:
        result = (
            service.users()
            .messages()
            .list(userId="me", q=query, maxResults=max_emails)
            .execute()
        )
        messages = result.get("messages", [])
    except Exception as e:
        logger.error(f"Gmail list failed for {student_id}: {e}")
        return 0

    for msg_ref in messages:
        try:
            msg = (
                service.users()
                .messages()
                .get(userId="me", id=msg_ref["id"], format="full")
                .execute()
            )
            count += await _process_message(service, student_id, msg)
        except Exception as e:
            logger.warning(f"Failed to process message {msg_ref['id']}: {e}")

    logger.info(f"Gmail sync complete for {student_id}: {count} emails upserted")
    return count


async def _process_message(service, student_id: str, msg: dict) -> int:
    """Process one Gmail message: store in emails + items tables."""
    headers    = msg.get("payload", {}).get("headers", [])
    message_id = msg["id"]
    thread_id  = msg.get("threadId", "")
    snippet    = msg.get("snippet", "")
    label_ids  = msg.get("labelIds", [])
    is_read    = "UNREAD" not in label_ids

    subject      = _get_header(headers, "Subject") or "(no subject)"
    raw_sender   = _get_header(headers, "From")
    raw_to       = _get_header(headers, "To")
    sender_name, sender_email = _parse_sender(raw_sender)

    # Recipients (To: field)
    recipients = [r.strip() for r in raw_to.split(",") if r.strip()] if raw_to else []

    # Parse received timestamp
    internal_ms  = int(msg.get("internalDate", 0))
    received_at  = datetime.fromtimestamp(internal_ms / 1000, tz=timezone.utc)

    # Clean body
    body_text = _clean_body(msg.get("payload", {}), snippet)

    # Extract academic/document links from body
    doc_links = _extract_doc_links(body_text)

    # Collect attachment metadata
    attach_parts: list[dict] = []
    _collect_attachment_parts(msg.get("payload", {}), attach_parts)
    has_attachments  = len(attach_parts) > 0
    attachment_count = len(attach_parts) + len(doc_links)

    # ── 1. Upsert into items (RAG layer) first to get item_id ────────────
    # Embed subject + sender + body for semantic search
    embed_text_for_rag = f"Subject: {subject}\nFrom: {sender_name} <{sender_email}>\n\n{body_text[:1000]}"

    item = await upsert_item({
        "student_id": student_id,
        "source":     "gmail",
        "source_id":  message_id,
        "raw_content": body_text,
        "title":      subject,
        "metadata": {
            "sender_name":    sender_name,
            "sender_email":   sender_email,
            "received_at":    received_at.isoformat(),
            "thread_id":      thread_id,
            "labels":         label_ids,
            "has_attachments": has_attachments,
            "doc_links":      doc_links[:5],   # store up to 5 doc links
        },
    })
    item_id = item["id"]

    # ── 2. Upsert into emails (structured store) ──────────────────────────
    email_row = await upsert_email({
        "student_id":       student_id,
        "item_id":          item_id,
        "message_id":       message_id,
        "thread_id":        thread_id,
        "subject":          subject,
        "sender_name":      sender_name,
        "sender_email":     sender_email,
        "recipients":       recipients,
        "received_at":      received_at.isoformat(),
        "body_text":        body_text,
        "snippet":          snippet,
        "labels":           label_ids,
        "has_attachments":  has_attachments,
        "attachment_count": attachment_count,
        "is_read":          is_read,
    })
    email_id = email_row["id"]

    # ── 3. Process file attachments ───────────────────────────────────────
    for part in attach_parts:
        await _process_file_attachment(
            service, student_id, email_id, item_id,
            message_id, subject, sender_name, part
        )

    # ── 4. Store doc links as link-type attachments ───────────────────────
    for url in doc_links:
        fname = url.split("/")[-1][:80] or "link"
        await upsert_email_attachment({
            "email_id":        email_id,
            "student_id":      student_id,
            "filename":        fname,
            "mime_type":       "text/uri-list",
            "attachment_type": "link",
            "url":             url,
            "extracted_text":  f"Link from email: {subject}\nURL: {url}",
        })

    return 1


async def _process_file_attachment(
    service, student_id: str, email_id: str, parent_item_id: str,
    message_id: str, subject: str, sender_name: str, part: dict
) -> None:
    filename   = part["filename"]
    mime_type  = part["mime_type"]
    size_bytes = part["size_bytes"]

    is_image    = mime_type.startswith(IMAGE_MIME_PREFIX)
    can_extract = (
        mime_type in TEXT_MIME_TYPES or
        filename.lower().endswith((".pdf", ".docx", ".doc", ".txt", ".md", ".csv"))
    )

    extracted_text = ""

    # Download and extract text (skip images and oversized files)
    if can_extract and size_bytes <= MAX_ATTACH_BYTES and size_bytes > 0:
        try:
            attach_data = (
                service.users()
                .messages()
                .attachments()
                .get(userId="me", messageId=message_id, id=part["attachment_id"])
                .execute()
            )
            raw_bytes = base64.urlsafe_b64decode(
                attach_data.get("data", "") + "=="
            )
            extracted_text = _extract_text_from_bytes(raw_bytes, mime_type, filename)
        except Exception as e:
            logger.warning(f"Attachment download failed ({filename}): {e}")

    attachment_type = "image" if is_image else "file"

    att_row = await upsert_email_attachment({
        "email_id":        email_id,
        "student_id":      student_id,
        "filename":        filename,
        "mime_type":       mime_type,
        "size_bytes":      size_bytes,
        "extracted_text":  extracted_text,
        "attachment_type": attachment_type,
    })

    # Create items row for attachments with extracted text (for RAG)
    if extracted_text.strip():
        att_item = await upsert_item({
            "student_id": student_id,
            "source":     "gmail_attachment",
            "source_id":  f"{message_id}_{filename}",
            "raw_content": extracted_text,
            "title":      f"[Attachment] {filename}",
            "metadata": {
                "email_id":       email_id,
                "parent_item_id": parent_item_id,
                "filename":       filename,
                "mime_type":      mime_type,
                "sender_name":    sender_name,
                "parent_subject": subject,
            },
        })
        # Link items row back to attachment
        from app.db.supabase import get_supabase
        db = get_supabase()
        db.table("email_attachments").update({"item_id": att_item["id"]}).eq("id", att_row["id"]).execute()
