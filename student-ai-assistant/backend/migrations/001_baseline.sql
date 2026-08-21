-- ============================================================================
-- 001_baseline — consolidated schema for Student AI Assistant
--
-- Replaces the four overlapping migrations archived under migrations/_archive/.
-- The original Supabase project was lost, so there is no data to preserve and
-- this is a clean baseline rather than an upgrade path.
--
-- Applies identically to local Postgres (pgvector/pgvector:pg16) and Supabase.
-- Idempotent: safe to re-run.
-- ============================================================================

CREATE EXTENSION IF NOT EXISTS vector;

-- pg_trgm powers fuzzy sender-name matching ("/emails from ajey" finding
-- "Ajay"). It ships with Supabase and with pgvector/pgvector:pg16, but not with
-- every minimal build, and it is an optimisation rather than a requirement —
-- the sender filter falls back to ILIKE without it. Degrade instead of failing.
DO $ext$
BEGIN
    CREATE EXTENSION IF NOT EXISTS pg_trgm;
EXCEPTION WHEN OTHERS THEN
    RAISE NOTICE 'pg_trgm unavailable (%) — fuzzy sender search will use ILIKE', SQLERRM;
END;
$ext$;

-- ─── updated_at trigger helper ───────────────────────────────────────────────
CREATE OR REPLACE FUNCTION set_updated_at()
RETURNS TRIGGER AS $fn$
BEGIN
    NEW.updated_at = now();
    RETURN NEW;
END;
$fn$ LANGUAGE plpgsql;


-- ═══════════════════════════════════════════════════════════════════════════
-- students
-- ═══════════════════════════════════════════════════════════════════════════
CREATE TABLE IF NOT EXISTS students (
    id                   UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    google_id            TEXT UNIQUE NOT NULL,
    email                TEXT NOT NULL,
    name                 TEXT,
    year                 SMALLINT CHECK (year BETWEEN 1 AND 6),
    branch               TEXT,                    -- CS, EE, ME, CE, PH, MA ...

    -- Telegram linking.  telegram_link_token is a one-shot nonce the web app
    -- issues; the student sends it as `/start <token>` to bind their chat.
    telegram_chat_id     BIGINT UNIQUE,
    telegram_link_token  TEXT UNIQUE,
    telegram_linked_at   TIMESTAMPTZ,
    digest_time          TIME NOT NULL DEFAULT '07:30',

    -- Google OAuth tokens, Fernet-encrypted at rest (app/utils/crypto.py).
    -- Refresh tokens do not expire; plaintext storage here would mean a single
    -- database leak grants persistent mailbox access. Never revert to JSONB.
    google_tokens_enc    TEXT,
    google_scopes        TEXT[] NOT NULL DEFAULT '{}',

    -- Per-source consent. Gmail is a RESTRICTED scope and is opt-in separately
    -- from the sensitive Classroom/Calendar scopes.
    gmail_enabled        BOOLEAN NOT NULL DEFAULT FALSE,

    -- DPDP Act 2023: record of explicit consent + soft-delete for erasure requests.
    consent_at           TIMESTAMPTZ,
    consent_version      TEXT,
    deleted_at           TIMESTAMPTZ,

    created_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at           TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS students_email_idx    ON students (lower(email));
CREATE INDEX IF NOT EXISTS students_active_idx   ON students (id) WHERE deleted_at IS NULL;

DROP TRIGGER IF EXISTS students_set_updated_at ON students;
CREATE TRIGGER students_set_updated_at
    BEFORE UPDATE ON students
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();


-- ═══════════════════════════════════════════════════════════════════════════
-- items — one unified row per piece of content, from any source
-- ═══════════════════════════════════════════════════════════════════════════
CREATE TABLE IF NOT EXISTS items (
    id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    student_id       UUID NOT NULL REFERENCES students(id) ON DELETE CASCADE,

    source           TEXT NOT NULL,        -- classroom|calendar|gmail|gmail_attachment|website|telegram
    source_id        TEXT NOT NULL,        -- upstream id; dedup key
    raw_content      TEXT NOT NULL DEFAULT '',

    -- Populated by the intelligence pipeline
    category         TEXT,                 -- academic|admin|event|transport|mess|placement|hostel|general
    title            TEXT,
    summary          TEXT,
    deadline         TIMESTAMPTZ,
    priority         TEXT NOT NULL DEFAULT 'LOW' CHECK (priority IN ('HIGH','MEDIUM','LOW')),
    relevance_score  REAL NOT NULL DEFAULT 0.5 CHECK (relevance_score BETWEEN 0 AND 1),
    confidence       REAL NOT NULL DEFAULT 1.0 CHECK (confidence BETWEEN 0 AND 1),

    is_read          BOOLEAN NOT NULL DEFAULT FALSE,
    is_actioned      BOOLEAN NOT NULL DEFAULT FALSE,

    embedding        vector(768),
    processed_at     TIMESTAMPTZ,          -- NULL = pipeline has not run yet
    metadata         JSONB NOT NULL DEFAULT '{}',

    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT now(),

    UNIQUE (student_id, source, source_id)
);

-- Full-text search vector, maintained by Postgres rather than the app so it can
-- never drift from the row. Weighted: title > summary > body.
ALTER TABLE items DROP COLUMN IF EXISTS search_vector;
ALTER TABLE items ADD COLUMN search_vector tsvector
    GENERATED ALWAYS AS (
        setweight(to_tsvector('english', coalesce(title,   '')), 'A') ||
        setweight(to_tsvector('english', coalesce(summary, '')), 'B') ||
        setweight(to_tsvector('english', left(coalesce(raw_content, ''), 200000)), 'C')
    ) STORED;

CREATE INDEX IF NOT EXISTS items_student_created_idx ON items (student_id, created_at DESC);
CREATE INDEX IF NOT EXISTS items_deadline_idx        ON items (student_id, deadline) WHERE deadline IS NOT NULL;
CREATE INDEX IF NOT EXISTS items_priority_idx        ON items (student_id, priority);
CREATE INDEX IF NOT EXISTS items_unprocessed_idx     ON items (student_id) WHERE processed_at IS NULL;
CREATE INDEX IF NOT EXISTS items_fts_idx             ON items USING gin (search_vector);

-- HNSW rather than IVFFlat: IVFFlat must be built on populated data to pick
-- cluster centroids, and the previous schema created it on an empty table with
-- lists=10, which degrades to near-sequential scan. HNSW needs no training and
-- stays correct as rows arrive.
CREATE INDEX IF NOT EXISTS items_embedding_idx ON items
    USING hnsw (embedding vector_cosine_ops) WITH (m = 16, ef_construction = 64);

DROP TRIGGER IF EXISTS items_set_updated_at ON items;
CREATE TRIGGER items_set_updated_at
    BEFORE UPDATE ON items
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();


-- ═══════════════════════════════════════════════════════════════════════════
-- deadlines — the radar
-- ═══════════════════════════════════════════════════════════════════════════
CREATE TABLE IF NOT EXISTS deadlines (
    id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    student_id        UUID NOT NULL REFERENCES students(id) ON DELETE CASCADE,
    item_id           UUID REFERENCES items(id) ON DELETE SET NULL,

    -- Stable identity for a deadline across re-syncs, e.g. 'classroom:9f2c1'.
    -- Without this the previous schema's bare UPSERT inserted a fresh row on
    -- every 2-hourly poll, which reset the alert flags and re-fired every
    -- reminder. Deadline correctness is Non-Negotiable Rule #1.
    dedup_key         TEXT NOT NULL,

    title             TEXT NOT NULL,
    due_at            TIMESTAMPTZ NOT NULL,
    source            TEXT NOT NULL,

    -- FALSE => extracted by the LLM below the 0.8 confidence bar; the UI must
    -- ask the student to confirm before it is ever treated as authoritative.
    confirmed         BOOLEAN NOT NULL DEFAULT FALSE,
    confidence        REAL NOT NULL DEFAULT 1.0,
    dismissed         BOOLEAN NOT NULL DEFAULT FALSE,

    calendar_event_id TEXT,

    alert_sent_48h    BOOLEAN NOT NULL DEFAULT FALSE,
    alert_sent_24h    BOOLEAN NOT NULL DEFAULT FALSE,
    alert_sent_6h     BOOLEAN NOT NULL DEFAULT FALSE,

    created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT now(),

    UNIQUE (student_id, dedup_key)
);

CREATE INDEX IF NOT EXISTS deadlines_student_due_idx ON deadlines (student_id, due_at)
    WHERE dismissed = FALSE;
CREATE INDEX IF NOT EXISTS deadlines_due_idx ON deadlines (due_at) WHERE dismissed = FALSE;

DROP TRIGGER IF EXISTS deadlines_set_updated_at ON deadlines;
CREATE TRIGGER deadlines_set_updated_at
    BEFORE UPDATE ON deadlines
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();


-- ═══════════════════════════════════════════════════════════════════════════
-- emails + attachments (Gmail — RESTRICTED scope, see docs/PRIVACY.md)
-- ═══════════════════════════════════════════════════════════════════════════
CREATE TABLE IF NOT EXISTS emails (
    id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    student_id       UUID NOT NULL REFERENCES students(id) ON DELETE CASCADE,
    item_id          UUID REFERENCES items(id) ON DELETE SET NULL,

    message_id       TEXT NOT NULL,
    thread_id        TEXT,
    subject          TEXT NOT NULL DEFAULT '',
    sender_name      TEXT NOT NULL DEFAULT '',
    sender_email     TEXT NOT NULL DEFAULT '',
    recipients       TEXT[] NOT NULL DEFAULT '{}',
    received_at      TIMESTAMPTZ NOT NULL,
    body_text        TEXT NOT NULL DEFAULT '',
    snippet          TEXT NOT NULL DEFAULT '',
    labels           TEXT[] NOT NULL DEFAULT '{}',
    has_attachments  BOOLEAN NOT NULL DEFAULT FALSE,
    attachment_count INTEGER NOT NULL DEFAULT 0,
    is_read          BOOLEAN NOT NULL DEFAULT FALSE,

    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),

    UNIQUE (student_id, message_id)
);

ALTER TABLE emails DROP COLUMN IF EXISTS search_vector;
ALTER TABLE emails ADD COLUMN search_vector tsvector
    GENERATED ALWAYS AS (
        setweight(to_tsvector('english', coalesce(subject, '')),     'A') ||
        setweight(to_tsvector('english', coalesce(sender_name, '')), 'B') ||
        setweight(to_tsvector('english', left(coalesce(body_text, ''), 200000)), 'C')
    ) STORED;

CREATE INDEX IF NOT EXISTS emails_student_received_idx ON emails (student_id, received_at DESC);
CREATE INDEX IF NOT EXISTS emails_sender_email_idx     ON emails (student_id, lower(sender_email));

-- Only if pg_trgm actually loaded (see the extension block at the top).
DO $trgm$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'pg_trgm') THEN
        CREATE INDEX IF NOT EXISTS emails_sender_trgm_idx
            ON emails USING gin (sender_name gin_trgm_ops);
    END IF;
END;
$trgm$;

CREATE INDEX IF NOT EXISTS emails_fts_idx              ON emails USING gin (search_vector);

CREATE TABLE IF NOT EXISTS email_attachments (
    id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    email_id         UUID NOT NULL REFERENCES emails(id) ON DELETE CASCADE,
    student_id       UUID NOT NULL REFERENCES students(id) ON DELETE CASCADE,
    item_id          UUID REFERENCES items(id) ON DELETE SET NULL,

    filename         TEXT NOT NULL DEFAULT '',
    mime_type        TEXT NOT NULL DEFAULT '',
    size_bytes       BIGINT NOT NULL DEFAULT 0,
    extracted_text   TEXT NOT NULL DEFAULT '',
    attachment_type  TEXT NOT NULL DEFAULT 'file' CHECK (attachment_type IN ('file','link','image')),
    url              TEXT,

    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),

    UNIQUE (email_id, filename)
);

CREATE INDEX IF NOT EXISTS email_attachments_email_idx   ON email_attachments (email_id);
CREATE INDEX IF NOT EXISTS email_attachments_student_idx ON email_attachments (student_id);


-- ═══════════════════════════════════════════════════════════════════════════
-- alerts — delivery history, prevents duplicate sends
-- ═══════════════════════════════════════════════════════════════════════════
CREATE TABLE IF NOT EXISTS alerts (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    student_id   UUID NOT NULL REFERENCES students(id) ON DELETE CASCADE,
    deadline_id  UUID REFERENCES deadlines(id) ON DELETE SET NULL,
    channel      TEXT NOT NULL,          -- telegram|push|email
    alert_type   TEXT NOT NULL,          -- digest|deadline_48h|deadline_24h|deadline_6h|overload
    delivered    BOOLEAN NOT NULL DEFAULT TRUE,
    sent_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS alerts_student_sent_idx ON alerts (student_id, sent_at DESC);

-- One digest per student per calendar day, enforced by the database rather than
-- by hoping the scheduler never double-fires.
CREATE UNIQUE INDEX IF NOT EXISTS alerts_one_digest_per_day_idx
    ON alerts (student_id, alert_type, ((sent_at AT TIME ZONE 'Asia/Kolkata')::date))
    WHERE alert_type = 'digest';


-- ═══════════════════════════════════════════════════════════════════════════
-- extraction_feedback — the moat: every correction is training data
-- ═══════════════════════════════════════════════════════════════════════════
CREATE TABLE IF NOT EXISTS extraction_feedback (
    id                 UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    item_id            UUID REFERENCES items(id) ON DELETE CASCADE,
    deadline_id        UUID REFERENCES deadlines(id) ON DELETE CASCADE,
    student_id         UUID REFERENCES students(id) ON DELETE SET NULL,

    was_correct        BOOLEAN NOT NULL,
    corrected_deadline TIMESTAMPTZ,
    corrected_category TEXT,
    notes              TEXT,

    -- What the model actually produced, kept verbatim so a correction remains
    -- interpretable after prompts and models change underneath it.
    model_output       JSONB NOT NULL DEFAULT '{}',
    model_name         TEXT,

    created_at         TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS feedback_item_idx    ON extraction_feedback (item_id);
CREATE INDEX IF NOT EXISTS feedback_created_idx ON extraction_feedback (created_at DESC);


-- ═══════════════════════════════════════════════════════════════════════════
-- schema_migrations — applied-migration ledger
-- ═══════════════════════════════════════════════════════════════════════════
CREATE TABLE IF NOT EXISTS schema_migrations (
    version     TEXT PRIMARY KEY,
    checksum    TEXT NOT NULL,
    applied_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
