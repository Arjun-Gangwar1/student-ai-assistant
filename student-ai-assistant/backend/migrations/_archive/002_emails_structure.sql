-- ============================================================
-- Migration 002: Structured email storage + 768-dim embeddings
-- Run this in Supabase SQL Editor
-- ============================================================

-- ── 1. emails table ──────────────────────────────────────────
CREATE TABLE IF NOT EXISTS emails (
    id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    student_id       UUID REFERENCES students(id) ON DELETE CASCADE,
    item_id          UUID REFERENCES items(id) ON DELETE SET NULL,
    message_id       TEXT NOT NULL,
    thread_id        TEXT,
    subject          TEXT NOT NULL DEFAULT '',
    sender_name      TEXT DEFAULT '',
    sender_email     TEXT DEFAULT '',
    recipients       TEXT[] DEFAULT '{}',
    received_at      TIMESTAMPTZ NOT NULL,
    body_text        TEXT DEFAULT '',
    snippet          TEXT DEFAULT '',
    labels           TEXT[] DEFAULT '{}',
    has_attachments  BOOLEAN DEFAULT FALSE,
    attachment_count INT DEFAULT 0,
    is_read          BOOLEAN DEFAULT FALSE,
    created_at       TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(student_id, message_id)
);

CREATE INDEX IF NOT EXISTS idx_emails_student_received
    ON emails(student_id, received_at DESC);
CREATE INDEX IF NOT EXISTS idx_emails_sender_email
    ON emails(student_id, sender_email);
CREATE INDEX IF NOT EXISTS idx_emails_sender_name
    ON emails(student_id, lower(sender_name));
CREATE INDEX IF NOT EXISTS idx_emails_subject_fts
    ON emails USING gin(to_tsvector('english', coalesce(subject, '')));
CREATE INDEX IF NOT EXISTS idx_emails_body_fts
    ON emails USING gin(to_tsvector('english', coalesce(body_text, '')));

-- ── 2. email_attachments table ───────────────────────────────
CREATE TABLE IF NOT EXISTS email_attachments (
    id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    email_id         UUID REFERENCES emails(id) ON DELETE CASCADE,
    student_id       UUID REFERENCES students(id) ON DELETE CASCADE,
    item_id          UUID REFERENCES items(id) ON DELETE SET NULL,
    filename         TEXT,
    mime_type        TEXT,
    size_bytes       INT DEFAULT 0,
    extracted_text   TEXT DEFAULT '',
    attachment_type  TEXT DEFAULT 'file',  -- 'file' | 'link' | 'image'
    url              TEXT,
    embedding        vector(768),
    created_at       TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(email_id, filename)
);

-- ── 3. Upgrade items.embedding: 384 → 768 dims ───────────────
-- This clears all existing embeddings (they will be re-generated)
ALTER TABLE items ALTER COLUMN embedding TYPE vector(768) USING NULL;

-- ── 4. Drop + recreate match_items with new dims ─────────────
DROP FUNCTION IF EXISTS match_items(vector, uuid, int, float);
DROP FUNCTION IF EXISTS match_items(vector(384), uuid, int, float);

CREATE OR REPLACE FUNCTION match_items(
    query_embedding   vector(768),
    match_student_id  UUID,
    match_count       INT     DEFAULT 10,
    similarity_threshold FLOAT DEFAULT 0.3
)
RETURNS TABLE (
    id               UUID,
    student_id       UUID,
    source           TEXT,
    source_id        TEXT,
    raw_content      TEXT,
    category         TEXT,
    title            TEXT,
    summary          TEXT,
    deadline         TIMESTAMPTZ,
    priority         TEXT,
    relevance_score  FLOAT,
    confidence       FLOAT,
    is_read          BOOLEAN,
    is_actioned      BOOLEAN,
    metadata         JSONB,
    created_at       TIMESTAMPTZ,
    updated_at       TIMESTAMPTZ,
    similarity       FLOAT
)
LANGUAGE plpgsql
AS $$
BEGIN
    RETURN QUERY
    SELECT
        i.id,
        i.student_id,
        i.source,
        i.source_id,
        i.raw_content,
        i.category,
        i.title,
        i.summary,
        i.deadline,
        i.priority,
        i.relevance_score,
        i.confidence,
        i.is_read,
        i.is_actioned,
        i.metadata,
        i.created_at,
        i.updated_at,
        1 - (i.embedding <=> query_embedding) AS similarity
    FROM items i
    WHERE
        i.student_id = match_student_id
        AND i.embedding IS NOT NULL
        AND 1 - (i.embedding <=> query_embedding) > similarity_threshold
    ORDER BY i.embedding <=> query_embedding
    LIMIT match_count;
END;
$$;
