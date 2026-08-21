"""
All database access, as plain SQL over asyncpg.

Replaces app/db/supabase.py. Every function returns dicts (or None) so callers
are unchanged in shape, but the queries are now real SQL: upserts specify their
conflict target, the alert sweep joins students in one statement, and search
runs server-side.

Conventions:
  - Soft-deleted students (deleted_at IS NOT NULL) are invisible to every read.
  - Nothing here trusts a caller-supplied student_id for authorisation; that is
    enforced in app/api/deps.py before these are reached.
"""

import logging
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any

import asyncpg

from app.db.pool import acquire, to_vector_literal
from app.utils.crypto import decrypt_tokens, encrypt_tokens

logger = logging.getLogger(__name__)


def _row(record: asyncpg.Record | None) -> dict | None:
    return dict(record) if record is not None else None


def _rows(records: list[asyncpg.Record]) -> list[dict]:
    return [dict(r) for r in records]


# ═══════════════════════════════════════════════════════════════════════════
# Students
# ═══════════════════════════════════════════════════════════════════════════

STUDENT_PUBLIC_COLUMNS = """
    id, google_id, email, name, year, branch,
    telegram_chat_id, telegram_linked_at, digest_time,
    google_scopes, gmail_enabled, consent_at, consent_version,
    created_at, updated_at
"""


async def get_student(student_id: str) -> dict | None:
    async with acquire() as conn:
        return _row(
            await conn.fetchrow(
                f"SELECT {STUDENT_PUBLIC_COLUMNS} FROM students "
                "WHERE id = $1 AND deleted_at IS NULL",
                student_id,
            )
        )


async def get_student_by_google_id(google_id: str) -> dict | None:
    async with acquire() as conn:
        return _row(
            await conn.fetchrow(
                f"SELECT {STUDENT_PUBLIC_COLUMNS} FROM students "
                "WHERE google_id = $1 AND deleted_at IS NULL",
                google_id,
            )
        )


async def get_student_by_telegram_chat(chat_id: int) -> dict | None:
    async with acquire() as conn:
        return _row(
            await conn.fetchrow(
                f"SELECT {STUDENT_PUBLIC_COLUMNS} FROM students "
                "WHERE telegram_chat_id = $1 AND deleted_at IS NULL",
                chat_id,
            )
        )


async def upsert_student(
    google_id: str,
    email: str,
    name: str | None,
    scopes: list[str],
    consent_version: str | None = None,
) -> dict:
    """
    Create or refresh a student on login.

    A returning user keeps their year/branch/telegram link; only the fields that
    genuinely come from Google are overwritten. `deleted_at` is cleared so that
    logging in again after an erasure request reinstates the account.
    """
    async with acquire() as conn:
        return _row(
            await conn.fetchrow(
                f"""
                INSERT INTO students (google_id, email, name, google_scopes,
                                      consent_at, consent_version)
                VALUES ($1, $2, $3, $4,
                        CASE WHEN $5::text IS NULL THEN NULL ELSE now() END, $5)
                ON CONFLICT (google_id) DO UPDATE SET
                    email           = EXCLUDED.email,
                    name            = COALESCE(EXCLUDED.name, students.name),
                    google_scopes   = EXCLUDED.google_scopes,
                    consent_at      = COALESCE(students.consent_at, EXCLUDED.consent_at),
                    consent_version = COALESCE(EXCLUDED.consent_version, students.consent_version),
                    deleted_at      = NULL
                RETURNING {STUDENT_PUBLIC_COLUMNS}
                """,
                google_id,
                email,
                name,
                scopes,
                consent_version,
            )
        )


async def get_active_students(with_google_tokens: bool = False) -> list[dict]:
    """Every non-deleted student. `with_google_tokens` decrypts as it goes."""
    cols = STUDENT_PUBLIC_COLUMNS + (", google_tokens_enc" if with_google_tokens else "")
    async with acquire() as conn:
        records = await conn.fetch(
            f"SELECT {cols} FROM students WHERE deleted_at IS NULL ORDER BY created_at"
        )

    students = _rows(records)
    if not with_google_tokens:
        return students

    for student in students:
        blob = student.pop("google_tokens_enc", None)
        try:
            student["google_tokens"] = decrypt_tokens(blob)
        except Exception as exc:
            # One unreadable row must not abort a sync sweep over everyone else.
            logger.error("Token decryption failed for student %s: %s", student["id"], exc)
            student["google_tokens"] = None
    return students


async def get_student_with_tokens(student_id: str) -> dict | None:
    async with acquire() as conn:
        record = await conn.fetchrow(
            f"SELECT {STUDENT_PUBLIC_COLUMNS}, google_tokens_enc FROM students "
            "WHERE id = $1 AND deleted_at IS NULL",
            student_id,
        )
    student = _row(record)
    if student is None:
        return None
    student["google_tokens"] = decrypt_tokens(student.pop("google_tokens_enc", None))
    return student


async def set_student_tokens(student_id: str, tokens: dict) -> None:
    """Store OAuth tokens encrypted. Never write this column in plaintext."""
    async with acquire() as conn:
        await conn.execute(
            "UPDATE students SET google_tokens_enc = $2 WHERE id = $1",
            student_id,
            encrypt_tokens(tokens),
        )


async def update_access_token(student_id: str, access_token: str, expiry: str | None) -> None:
    """
    Persist a refreshed access token.

    google-auth refreshes in memory; without writing the result back, every
    worker run spends an extra round trip to Google re-refreshing a token it
    already had.
    """
    student = await get_student_with_tokens(student_id)
    if not student or not student.get("google_tokens"):
        return
    tokens = dict(student["google_tokens"])
    tokens["access_token"] = access_token
    if expiry:
        tokens["expiry"] = expiry
    await set_student_tokens(student_id, tokens)


async def update_student_profile(
    student_id: str, year: int | None, branch: str | None
) -> dict | None:
    async with acquire() as conn:
        return _row(
            await conn.fetchrow(
                f"UPDATE students SET year = $2, branch = $3 "
                f"WHERE id = $1 AND deleted_at IS NULL RETURNING {STUDENT_PUBLIC_COLUMNS}",
                student_id,
                year,
                branch,
            )
        )


async def set_gmail_enabled(student_id: str, enabled: bool) -> dict | None:
    async with acquire() as conn:
        return _row(
            await conn.fetchrow(
                f"UPDATE students SET gmail_enabled = $2 "
                f"WHERE id = $1 AND deleted_at IS NULL RETURNING {STUDENT_PUBLIC_COLUMNS}",
                student_id,
                enabled,
            )
        )


async def set_digest_time(student_id: str, digest_time: str) -> None:
    async with acquire() as conn:
        await conn.execute(
            "UPDATE students SET digest_time = $2::time WHERE id = $1",
            student_id,
            digest_time,
        )


# ─── Telegram linking ────────────────────────────────────────────────────────

async def issue_telegram_link_token(student_id: str) -> str:
    """
    Mint a single-use token the student sends as `/start <token>`.

    The previous webhook read `students.telegram_link_token`, a column that
    existed in no migration, so account linking could never have worked.
    """
    token = secrets.token_urlsafe(24)
    async with acquire() as conn:
        await conn.execute(
            "UPDATE students SET telegram_link_token = $2 WHERE id = $1",
            student_id,
            token,
        )
    return token


async def redeem_telegram_link_token(token: str, chat_id: int) -> dict | None:
    """
    Bind a Telegram chat to the student holding this token, and burn the token.

    Also clears the chat from any other student first: telegram_chat_id is UNIQUE,
    so re-linking a phone that previously belonged to another account would
    otherwise fail on the constraint instead of transferring.
    """
    async with acquire() as conn:
        async with conn.transaction():
            await conn.execute(
                "UPDATE students SET telegram_chat_id = NULL, telegram_linked_at = NULL "
                "WHERE telegram_chat_id = $1",
                chat_id,
            )
            return _row(
                await conn.fetchrow(
                    f"""
                    UPDATE students
                       SET telegram_chat_id    = $2,
                           telegram_linked_at  = now(),
                           telegram_link_token = NULL
                     WHERE telegram_link_token = $1
                       AND deleted_at IS NULL
                    RETURNING {STUDENT_PUBLIC_COLUMNS}
                    """,
                    token,
                    chat_id,
                )
            )


async def unlink_telegram(student_id: str) -> None:
    async with acquire() as conn:
        await conn.execute(
            "UPDATE students SET telegram_chat_id = NULL, telegram_linked_at = NULL "
            "WHERE id = $1",
            student_id,
        )


# ─── DPDP Act: erasure and portability ───────────────────────────────────────

async def soft_delete_student(student_id: str) -> None:
    """
    Mark for erasure and destroy the OAuth tokens immediately.

    Tokens go first and synchronously: the point of a deletion request is that
    access stops now, not whenever a purge job next runs. Content rows are
    removed by `purge_deleted_students`.
    """
    async with acquire() as conn:
        await conn.execute(
            """
            UPDATE students
               SET deleted_at = now(),
                   google_tokens_enc = NULL,
                   telegram_chat_id = NULL,
                   telegram_link_token = NULL
             WHERE id = $1
            """,
            student_id,
        )


async def purge_deleted_students(grace_days: int = 7) -> int:
    """
    Hard-delete students soft-deleted more than `grace_days` ago.

    The grace period exists so an accidental deletion is recoverable; after it,
    ON DELETE CASCADE removes items, deadlines, emails, alerts and feedback.
    """
    async with acquire() as conn:
        result = await conn.execute(
            "DELETE FROM students WHERE deleted_at IS NOT NULL "
            "AND deleted_at < now() - ($1 || ' days')::interval",
            str(grace_days),
        )
    count = int(result.split()[-1]) if result.startswith("DELETE") else 0
    if count:
        logger.info("Purged %d soft-deleted student(s)", count)
    return count


async def export_student_data(student_id: str) -> dict[str, Any]:
    """Everything held about one student, for a data-portability request."""
    async with acquire() as conn:
        student = await conn.fetchrow(
            f"SELECT {STUDENT_PUBLIC_COLUMNS} FROM students WHERE id = $1", student_id
        )
        items = await conn.fetch(
            "SELECT source, source_id, title, summary, category, priority, "
            "deadline, raw_content, metadata, created_at "
            "FROM items WHERE student_id = $1 ORDER BY created_at",
            student_id,
        )
        deadlines = await conn.fetch(
            "SELECT title, due_at, source, confirmed, confidence, created_at "
            "FROM deadlines WHERE student_id = $1 ORDER BY due_at",
            student_id,
        )
        emails = await conn.fetch(
            "SELECT subject, sender_name, sender_email, received_at, snippet, "
            "body_text, labels, attachment_count "
            "FROM emails WHERE student_id = $1 ORDER BY received_at",
            student_id,
        )
        alerts = await conn.fetch(
            "SELECT channel, alert_type, sent_at FROM alerts "
            "WHERE student_id = $1 ORDER BY sent_at",
            student_id,
        )

    return {
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "student": _row(student),
        "items": _rows(items),
        "deadlines": _rows(deadlines),
        "emails": _rows(emails),
        "alerts": _rows(alerts),
    }


# ═══════════════════════════════════════════════════════════════════════════
# Items
# ═══════════════════════════════════════════════════════════════════════════

async def upsert_item(
    student_id: str,
    source: str,
    source_id: str,
    raw_content: str,
    title: str | None = None,
    deadline: datetime | None = None,
    metadata: dict | None = None,
) -> dict:
    """
    Insert or refresh one item.

    When the upstream content actually changed, `processed_at` is reset to NULL
    so the intelligence pipeline reclassifies it. When it did not, processing
    state is left alone — otherwise every 2-hourly poll would re-run the LLM
    over unchanged rows and burn the Groq quota for no new information.
    """
    async with acquire() as conn:
        return _row(
            await conn.fetchrow(
                """
                INSERT INTO items (student_id, source, source_id, raw_content,
                                   title, deadline, metadata)
                VALUES ($1, $2, $3, $4, $5, $6, $7::jsonb)
                ON CONFLICT (student_id, source, source_id) DO UPDATE SET
                    raw_content  = EXCLUDED.raw_content,
                    title        = COALESCE(EXCLUDED.title, items.title),
                    deadline     = COALESCE(EXCLUDED.deadline, items.deadline),
                    metadata     = items.metadata || EXCLUDED.metadata,
                    processed_at = CASE
                        WHEN items.raw_content IS DISTINCT FROM EXCLUDED.raw_content
                        THEN NULL
                        ELSE items.processed_at
                    END
                RETURNING id, student_id, source, source_id, title, summary,
                          category, priority, deadline, processed_at, created_at
                """,
                student_id,
                source,
                source_id,
                raw_content,
                title,
                deadline,
                metadata or {},
            )
        )


async def get_unprocessed_items(student_id: str, limit: int = 50) -> list[dict]:
    async with acquire() as conn:
        return _rows(
            await conn.fetch(
                """
                SELECT id, student_id, source, source_id, raw_content, title,
                       deadline, metadata, created_at
                  FROM items
                 WHERE student_id = $1 AND processed_at IS NULL
                 ORDER BY created_at DESC
                 LIMIT $2
                """,
                student_id,
                limit,
            )
        )


async def count_unprocessed_items(student_id: str) -> int:
    async with acquire() as conn:
        return await conn.fetchval(
            "SELECT count(*) FROM items WHERE student_id = $1 AND processed_at IS NULL",
            student_id,
        )


async def save_item_analysis(
    item_id: str,
    category: str,
    priority: str,
    relevance_score: float,
    summary: str,
    embedding: list[float] | None,
    deadline: datetime | None = None,
    confidence: float | None = None,
) -> None:
    """Write back everything the intelligence pipeline produced, in one update."""
    async with acquire() as conn:
        await conn.execute(
            """
            UPDATE items
               SET category        = $2,
                   priority        = $3,
                   relevance_score = $4,
                   summary         = $5,
                   embedding       = CASE WHEN $6::text IS NULL
                                          THEN embedding ELSE $6::vector END,
                   deadline        = COALESCE(items.deadline, $7),
                   confidence      = COALESCE($8, items.confidence),
                   processed_at    = now()
             WHERE id = $1
            """,
            item_id,
            category,
            priority,
            relevance_score,
            summary,
            to_vector_literal(embedding) if embedding else None,
            deadline,
            confidence,
        )


async def list_items(
    student_id: str,
    priority: str | None = None,
    category: str | None = None,
    source: str | None = None,
    unread_only: bool = False,
    limit: int = 20,
    offset: int = 0,
) -> list[dict]:
    async with acquire() as conn:
        return _rows(
            await conn.fetch(
                """
                SELECT id, title, summary, source, category, priority,
                       relevance_score, confidence, deadline, is_read,
                       is_actioned, metadata, created_at
                  FROM items
                 WHERE student_id = $1
                   AND ($2::text IS NULL OR priority = $2)
                   AND ($3::text IS NULL OR category = $3)
                   AND ($4::text IS NULL OR source   = $4)
                   AND (NOT $5::boolean OR is_read = FALSE)
                 ORDER BY created_at DESC
                 LIMIT $6 OFFSET $7
                """,
                student_id,
                priority.upper() if priority else None,
                category.lower() if category else None,
                source.lower() if source else None,
                unread_only,
                limit,
                offset,
            )
        )


async def get_item(item_id: str, student_id: str) -> dict | None:
    """Scoped by student_id so one student can never read another's item."""
    async with acquire() as conn:
        return _row(
            await conn.fetchrow(
                "SELECT * FROM items WHERE id = $1 AND student_id = $2",
                item_id,
                student_id,
            )
        )


async def mark_item_read(item_id: str, student_id: str) -> bool:
    async with acquire() as conn:
        result = await conn.execute(
            "UPDATE items SET is_read = TRUE WHERE id = $1 AND student_id = $2",
            item_id,
            student_id,
        )
    return result.endswith("1")


async def get_high_priority_items(student_id: str, limit: int = 3) -> list[dict]:
    async with acquire() as conn:
        return _rows(
            await conn.fetch(
                """
                SELECT id, title, summary, source, category
                  FROM items
                 WHERE student_id = $1 AND is_read = FALSE AND priority = 'HIGH'
                 ORDER BY created_at DESC
                 LIMIT $2
                """,
                student_id,
                limit,
            )
        )


# ═══════════════════════════════════════════════════════════════════════════
# Deadlines
# ═══════════════════════════════════════════════════════════════════════════

async def upsert_deadline(
    student_id: str,
    dedup_key: str,
    title: str,
    due_at: datetime,
    source: str,
    item_id: str | None = None,
    confirmed: bool = False,
    confidence: float = 1.0,
    calendar_event_id: str | None = None,
) -> dict:
    """
    Insert or update one deadline, keyed on (student_id, dedup_key).

    The alert flags are reset only when `due_at` actually moves — a rescheduled
    deadline should re-alert, an unchanged one must not. The previous code
    upserted with no conflict target at all, so each poll inserted a duplicate
    row with fresh flags and every reminder fired again on every cycle.
    """
    async with acquire() as conn:
        return _row(
            await conn.fetchrow(
                """
                INSERT INTO deadlines (student_id, dedup_key, item_id, title,
                                       due_at, source, confirmed, confidence,
                                       calendar_event_id)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
                ON CONFLICT (student_id, dedup_key) DO UPDATE SET
                    title             = EXCLUDED.title,
                    due_at            = EXCLUDED.due_at,
                    item_id           = COALESCE(EXCLUDED.item_id, deadlines.item_id),
                    confidence        = EXCLUDED.confidence,
                    calendar_event_id = COALESCE(EXCLUDED.calendar_event_id,
                                                 deadlines.calendar_event_id),
                    -- A student's explicit confirmation outranks a re-sync.
                    confirmed      = deadlines.confirmed OR EXCLUDED.confirmed,
                    alert_sent_48h = CASE WHEN deadlines.due_at IS DISTINCT FROM EXCLUDED.due_at
                                          THEN FALSE ELSE deadlines.alert_sent_48h END,
                    alert_sent_24h = CASE WHEN deadlines.due_at IS DISTINCT FROM EXCLUDED.due_at
                                          THEN FALSE ELSE deadlines.alert_sent_24h END,
                    alert_sent_6h  = CASE WHEN deadlines.due_at IS DISTINCT FROM EXCLUDED.due_at
                                          THEN FALSE ELSE deadlines.alert_sent_6h END
                RETURNING *
                """,
                student_id,
                dedup_key,
                item_id,
                title,
                due_at,
                source,
                confirmed,
                confidence,
                calendar_event_id,
            )
        )


async def get_upcoming_deadlines(student_id: str, days: int = 7) -> list[dict]:
    async with acquire() as conn:
        return _rows(
            await conn.fetch(
                """
                SELECT id, item_id, title, due_at, source, confirmed, confidence,
                       calendar_event_id, created_at,
                       EXTRACT(EPOCH FROM (due_at - now())) / 3600 AS hours_left
                  FROM deadlines
                 WHERE student_id = $1
                   AND dismissed = FALSE
                   AND due_at >= now()
                   AND due_at <= now() + ($2 || ' days')::interval
                 ORDER BY due_at
                """,
                student_id,
                str(days),
            )
        )


async def confirm_deadline(
    deadline_id: str,
    student_id: str,
    confirmed: bool,
    corrected_due_at: datetime | None = None,
) -> dict | None:
    async with acquire() as conn:
        return _row(
            await conn.fetchrow(
                """
                UPDATE deadlines
                   SET confirmed = $3,
                       due_at    = COALESCE($4, due_at),
                       dismissed = CASE WHEN $3 = FALSE AND $4 IS NULL
                                        THEN TRUE ELSE dismissed END
                 WHERE id = $1 AND student_id = $2
                RETURNING *
                """,
                deadline_id,
                student_id,
                confirmed,
                corrected_due_at,
            )
        )


async def get_deadlines_needing_alert(alert_field: str) -> list[dict]:
    """
    Deadlines due within the alert window whose flag is still unset, joined to
    the student's Telegram chat in one query.

    `alert_field` is validated against a fixed set rather than interpolated
    blind — it names a column, so it cannot be a bound parameter.
    """
    windows = {"alert_sent_48h": 48, "alert_sent_24h": 24, "alert_sent_6h": 6}
    if alert_field not in windows:
        raise ValueError(f"unknown alert field {alert_field!r}")
    hours = windows[alert_field]

    async with acquire() as conn:
        return _rows(
            await conn.fetch(
                f"""
                SELECT d.id, d.student_id, d.title, d.due_at, d.source,
                       s.telegram_chat_id, s.name AS student_name
                  FROM deadlines d
                  JOIN students s ON s.id = d.student_id
                 WHERE d.{alert_field} = FALSE
                   AND d.dismissed = FALSE
                   AND s.deleted_at IS NULL
                   AND d.due_at > now()
                   AND d.due_at <= now() + interval '{hours} hours'
                 ORDER BY d.due_at
                """
            )
        )


async def mark_alert_sent(deadline_id: str, alert_field: str) -> None:
    if alert_field not in {"alert_sent_48h", "alert_sent_24h", "alert_sent_6h"}:
        raise ValueError(f"unknown alert field {alert_field!r}")
    async with acquire() as conn:
        await conn.execute(
            f"UPDATE deadlines SET {alert_field} = TRUE WHERE id = $1", deadline_id
        )


# ═══════════════════════════════════════════════════════════════════════════
# Alerts
# ═══════════════════════════════════════════════════════════════════════════

async def log_alert(
    student_id: str,
    alert_type: str,
    channel: str = "telegram",
    deadline_id: str | None = None,
    delivered: bool = True,
) -> bool:
    """
    Record a delivery. Returns False when a unique index rejected it — that is
    the daily-digest guard working, not an error.
    """
    async with acquire() as conn:
        try:
            await conn.execute(
                "INSERT INTO alerts (student_id, deadline_id, channel, alert_type, delivered) "
                "VALUES ($1, $2, $3, $4, $5)",
                student_id,
                deadline_id,
                channel,
                alert_type,
                delivered,
            )
            return True
        except asyncpg.UniqueViolationError:
            logger.debug("Duplicate %s alert suppressed for student %s", alert_type, student_id)
            return False


async def digest_already_sent_today(student_id: str) -> bool:
    async with acquire() as conn:
        return await conn.fetchval(
            """
            SELECT EXISTS (
                SELECT 1 FROM alerts
                 WHERE student_id = $1
                   AND alert_type = 'digest'
                   AND (sent_at AT TIME ZONE 'Asia/Kolkata')::date
                     = (now()  AT TIME ZONE 'Asia/Kolkata')::date
            )
            """,
            student_id,
        )


async def count_recent_alerts(student_id: str, alert_type: str, hours: int) -> int:
    async with acquire() as conn:
        return await conn.fetchval(
            "SELECT count(*) FROM alerts WHERE student_id = $1 AND alert_type = $2 "
            "AND sent_at > now() - ($3 || ' hours')::interval",
            student_id,
            alert_type,
            str(hours),
        )


# ═══════════════════════════════════════════════════════════════════════════
# Search / RAG
# ═══════════════════════════════════════════════════════════════════════════

async def hybrid_search(
    query_text: str,
    query_embedding: list[float],
    student_ids: list[str],
    limit: int = 8,
) -> list[dict]:
    """Vector + full-text retrieval fused with RRF, server-side."""
    async with acquire() as conn:
        return _rows(
            await conn.fetch(
                "SELECT * FROM hybrid_search_items($1::vector, $2, $3::uuid[], $4)",
                to_vector_literal(query_embedding),
                query_text,
                student_ids,
                limit,
            )
        )


async def semantic_search(
    query_embedding: list[float],
    student_ids: list[str],
    limit: int = 8,
    threshold: float = 0.3,
) -> list[dict]:
    async with acquire() as conn:
        return _rows(
            await conn.fetch(
                "SELECT * FROM match_items($1::vector, $2::uuid[], $3, $4)",
                to_vector_literal(query_embedding),
                student_ids,
                limit,
                threshold,
            )
        )


async def search_items_keyword(
    query_text: str, student_ids: list[str], limit: int = 8
) -> list[dict]:
    """
    Keyword-only retrieval — the degraded path used when embedding is
    unavailable (model still loading, or a sentence-transformers failure).
    Answering from full-text hits beats answering from nothing.
    """
    async with acquire() as conn:
        return _rows(
            await conn.fetch(
                """
                WITH q AS (SELECT websearch_to_tsquery('english', $1) AS tsq)
                SELECT i.id, i.student_id, i.source, i.source_id, i.title, i.summary,
                       i.raw_content, i.category, i.priority, i.relevance_score,
                       i.confidence, i.deadline, i.is_read, i.metadata, i.created_at,
                       ts_rank_cd(i.search_vector, q.tsq) AS rank
                  FROM items i, q
                 WHERE i.student_id = ANY ($2::uuid[])
                   AND q.tsq != ''::tsquery
                   AND i.search_vector @@ q.tsq
                 ORDER BY rank DESC, i.created_at DESC
                 LIMIT $3
                """,
                query_text,
                student_ids,
                limit,
            )
        )


async def find_near_duplicates(item_id: str, threshold: float = 0.92) -> list[dict]:
    async with acquire() as conn:
        return _rows(
            await conn.fetch(
                "SELECT * FROM find_similar_items($1, $2)", item_id, threshold
            )
        )


# ═══════════════════════════════════════════════════════════════════════════
# Emails
# ═══════════════════════════════════════════════════════════════════════════

async def upsert_email(student_id: str, item_id: str | None, **fields) -> dict:
    async with acquire() as conn:
        return _row(
            await conn.fetchrow(
                """
                INSERT INTO emails (student_id, item_id, message_id, thread_id,
                                    subject, sender_name, sender_email, recipients,
                                    received_at, body_text, snippet, labels,
                                    has_attachments, attachment_count, is_read)
                VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15)
                ON CONFLICT (student_id, message_id) DO UPDATE SET
                    item_id          = COALESCE(EXCLUDED.item_id, emails.item_id),
                    subject          = EXCLUDED.subject,
                    body_text        = EXCLUDED.body_text,
                    snippet          = EXCLUDED.snippet,
                    labels           = EXCLUDED.labels,
                    is_read          = EXCLUDED.is_read,
                    has_attachments  = EXCLUDED.has_attachments,
                    attachment_count = EXCLUDED.attachment_count
                RETURNING id, message_id, subject
                """,
                student_id,
                item_id,
                fields["message_id"],
                fields.get("thread_id"),
                fields.get("subject", ""),
                fields.get("sender_name", ""),
                fields.get("sender_email", ""),
                fields.get("recipients", []),
                fields["received_at"],
                fields.get("body_text", ""),
                fields.get("snippet", ""),
                fields.get("labels", []),
                fields.get("has_attachments", False),
                fields.get("attachment_count", 0),
                fields.get("is_read", False),
            )
        )


async def upsert_email_attachment(
    email_id: str, student_id: str, filename: str, **fields
) -> dict:
    async with acquire() as conn:
        return _row(
            await conn.fetchrow(
                """
                INSERT INTO email_attachments (email_id, student_id, item_id, filename,
                                               mime_type, size_bytes, extracted_text,
                                               attachment_type, url)
                VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9)
                ON CONFLICT (email_id, filename) DO UPDATE SET
                    item_id        = COALESCE(EXCLUDED.item_id, email_attachments.item_id),
                    extracted_text = EXCLUDED.extracted_text,
                    size_bytes     = EXCLUDED.size_bytes
                RETURNING id, filename
                """,
                email_id,
                student_id,
                fields.get("item_id"),
                filename,
                fields.get("mime_type", ""),
                fields.get("size_bytes", 0),
                fields.get("extracted_text", ""),
                fields.get("attachment_type", "file"),
                fields.get("url"),
            )
        )


async def link_attachment_item(attachment_id: str, item_id: str) -> None:
    async with acquire() as conn:
        await conn.execute(
            "UPDATE email_attachments SET item_id = $2 WHERE id = $1",
            attachment_id,
            item_id,
        )


async def list_emails(
    student_id: str,
    limit: int = 10,
    offset: int = 0,
    date: str | None = None,
    sender: str | None = None,
    subject: str | None = None,
) -> list[dict]:
    """
    Filtered listing. `date` is interpreted in IST, since a student asking for
    "emails from 3 August" means the Indian calendar day, not the UTC one.
    """
    async with acquire() as conn:
        return _rows(
            await conn.fetch(
                """
                SELECT id, subject, sender_name, sender_email, received_at,
                       snippet, labels, has_attachments, attachment_count,
                       is_read, item_id
                  FROM emails
                 WHERE student_id = $1
                   AND ($2::date IS NULL
                        OR (received_at AT TIME ZONE 'Asia/Kolkata')::date = $2::date)
                   AND ($3::text IS NULL
                        OR sender_name ILIKE '%' || $3 || '%'
                        OR sender_email ILIKE '%' || $3 || '%')
                   AND ($4::text IS NULL OR subject ILIKE '%' || $4 || '%')
                 ORDER BY received_at DESC
                 LIMIT $5 OFFSET $6
                """,
                student_id,
                date,
                sender,
                subject,
                limit,
                offset,
            )
        )


async def search_emails(student_id: str, query: str, limit: int = 10) -> list[dict]:
    """Full-text search over the generated search_vector (subject + sender + body)."""
    async with acquire() as conn:
        return _rows(
            await conn.fetch(
                """
                WITH q AS (SELECT websearch_to_tsquery('english', $2) AS tsq)
                SELECT e.id, e.subject, e.sender_name, e.sender_email,
                       e.received_at, e.snippet, e.has_attachments, e.is_read,
                       ts_rank_cd(e.search_vector, q.tsq) AS rank
                  FROM emails e, q
                 WHERE e.student_id = $1
                   AND q.tsq != ''::tsquery
                   AND e.search_vector @@ q.tsq
                 ORDER BY rank DESC, e.received_at DESC
                 LIMIT $3
                """,
                student_id,
                query,
                limit,
            )
        )


async def get_email_detail(email_id: str, student_id: str) -> dict | None:
    async with acquire() as conn:
        email = _row(
            await conn.fetchrow(
                "SELECT * FROM emails WHERE id = $1 AND student_id = $2",
                email_id,
                student_id,
            )
        )
        if email is None:
            return None
        email.pop("search_vector", None)
        email["attachments"] = _rows(
            await conn.fetch(
                "SELECT id, filename, mime_type, size_bytes, attachment_type, url, "
                "extracted_text FROM email_attachments WHERE email_id = $1 "
                "ORDER BY filename",
                email_id,
            )
        )
        return email


async def count_emails(student_id: str) -> int:
    async with acquire() as conn:
        return await conn.fetchval(
            "SELECT count(*) FROM emails WHERE student_id = $1", student_id
        )


async def latest_email_timestamp(student_id: str) -> datetime | None:
    """Newest email held, so a sync can fetch only what arrived since."""
    async with acquire() as conn:
        return await conn.fetchval(
            "SELECT max(received_at) FROM emails WHERE student_id = $1", student_id
        )


# ═══════════════════════════════════════════════════════════════════════════
# Feedback (the moat)
# ═══════════════════════════════════════════════════════════════════════════

async def save_feedback(
    student_id: str,
    was_correct: bool,
    item_id: str | None = None,
    deadline_id: str | None = None,
    corrected_deadline: datetime | None = None,
    corrected_category: str | None = None,
    notes: str | None = None,
    model_output: dict | None = None,
    model_name: str | None = None,
) -> dict:
    async with acquire() as conn:
        return _row(
            await conn.fetchrow(
                """
                INSERT INTO extraction_feedback
                    (student_id, item_id, deadline_id, was_correct, corrected_deadline,
                     corrected_category, notes, model_output, model_name)
                VALUES ($1,$2,$3,$4,$5,$6,$7,$8::jsonb,$9)
                RETURNING id, created_at
                """,
                student_id,
                item_id,
                deadline_id,
                was_correct,
                corrected_deadline,
                corrected_category,
                notes,
                model_output or {},
                model_name,
            )
        )


async def feedback_stats() -> dict:
    """Extraction accuracy over time — the metric that says whether to keep the LLM."""
    async with acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT count(*) AS total,
                   count(*) FILTER (WHERE was_correct)     AS correct,
                   count(*) FILTER (WHERE NOT was_correct) AS incorrect,
                   count(*) FILTER (WHERE created_at > now() - interval '7 days') AS last_7d
              FROM extraction_feedback
            """
        )
    stats = dict(row)
    stats["accuracy"] = (stats["correct"] / stats["total"]) if stats["total"] else None
    return stats
