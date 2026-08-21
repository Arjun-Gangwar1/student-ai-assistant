-- Enable pgvector
CREATE EXTENSION IF NOT EXISTS vector;

-- ─── Students ────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS students (
    id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    google_id         TEXT UNIQUE NOT NULL,
    email             TEXT NOT NULL,
    name              TEXT,
    year              INT CHECK (year BETWEEN 1 AND 5),
    branch            TEXT,         -- CS, EE, ME, CE, PH ...
    telegram_chat_id  BIGINT,
    digest_time       TIME DEFAULT '07:30',
    google_tokens     JSONB,        -- encrypted OAuth token blob
    created_at        TIMESTAMPTZ DEFAULT now(),
    updated_at        TIMESTAMPTZ DEFAULT now()
);

-- ─── Items (unified across all sources) ──────────────────────────────────────
CREATE TABLE IF NOT EXISTS items (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    student_id      UUID NOT NULL REFERENCES students(id) ON DELETE CASCADE,
    source          TEXT NOT NULL,         -- classroom|calendar|gmail|website|telegram
    source_id       TEXT,                  -- original platform ID (dedup key)
    raw_content     TEXT,
    category        TEXT,                  -- academic|admin|event|transport|mess|placement|hostel|general
    title           TEXT,
    summary         TEXT,
    deadline        TIMESTAMPTZ,
    priority        TEXT DEFAULT 'LOW',    -- HIGH|MEDIUM|LOW
    relevance_score FLOAT DEFAULT 0.5,
    confidence      FLOAT DEFAULT 1.0,     -- LLM extraction confidence
    is_read         BOOL DEFAULT false,
    is_actioned     BOOL DEFAULT false,
    embedding       vector(768),
    metadata        JSONB DEFAULT '{}',
    created_at      TIMESTAMPTZ DEFAULT now(),
    updated_at      TIMESTAMPTZ DEFAULT now(),
    UNIQUE (student_id, source, source_id)
);

CREATE INDEX IF NOT EXISTS items_student_id_idx ON items(student_id);
CREATE INDEX IF NOT EXISTS items_deadline_idx ON items(deadline) WHERE deadline IS NOT NULL;
CREATE INDEX IF NOT EXISTS items_priority_idx ON items(priority);
CREATE INDEX IF NOT EXISTS items_embedding_idx ON items
    USING ivfflat (embedding vector_cosine_ops) WITH (lists = 10);

-- Full-text search index on title + summary
CREATE INDEX IF NOT EXISTS items_fts_idx ON items
    USING gin(to_tsvector('english', coalesce(title, '') || ' ' || coalesce(summary, '')));

-- ─── Deadlines (extracted + confirmed) ───────────────────────────────────────
CREATE TABLE IF NOT EXISTS deadlines (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    student_id          UUID NOT NULL REFERENCES students(id) ON DELETE CASCADE,
    item_id             UUID REFERENCES items(id) ON DELETE SET NULL,
    title               TEXT NOT NULL,
    due_at              TIMESTAMPTZ NOT NULL,
    source              TEXT,
    confirmed           BOOL DEFAULT false,
    calendar_event_id   TEXT,
    alert_sent_48h      BOOL DEFAULT false,
    alert_sent_24h      BOOL DEFAULT false,
    alert_sent_6h       BOOL DEFAULT false,
    created_at          TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX IF NOT EXISTS deadlines_student_due_idx ON deadlines(student_id, due_at);
CREATE INDEX IF NOT EXISTS deadlines_due_at_idx ON deadlines(due_at);

-- ─── Alert history ────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS alerts (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    student_id   UUID NOT NULL REFERENCES students(id) ON DELETE CASCADE,
    deadline_id  UUID REFERENCES deadlines(id) ON DELETE SET NULL,
    channel      TEXT NOT NULL,    -- telegram|push|email
    alert_type   TEXT NOT NULL,    -- digest|deadline_48h|deadline_24h|deadline_6h|overload
    sent_at      TIMESTAMPTZ DEFAULT now()
);

-- ─── Extraction feedback (moat builder) ───────────────────────────────────────
CREATE TABLE IF NOT EXISTS extraction_feedback (
    id                   UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    item_id              UUID REFERENCES items(id) ON DELETE CASCADE,
    student_id           UUID REFERENCES students(id) ON DELETE CASCADE,
    was_correct          BOOL,
    corrected_deadline   TIMESTAMPTZ,
    corrected_category   TEXT,
    notes                TEXT,
    created_at           TIMESTAMPTZ DEFAULT now()
);

-- ─── Auto-update updated_at ───────────────────────────────────────────────────
CREATE OR REPLACE FUNCTION update_updated_at_column()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = now();
    RETURN NEW;
END;
$$ language 'plpgsql';

CREATE TRIGGER update_students_updated_at
    BEFORE UPDATE ON students
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

CREATE TRIGGER update_items_updated_at
    BEFORE UPDATE ON items
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();
