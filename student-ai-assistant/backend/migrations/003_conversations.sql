-- ============================================================================
-- 003_conversations — persistent chat history
--
-- Until now the chat was stateless: history lived in React state and vanished on
-- navigation. Every question started from nothing, so follow-ups like "what
-- about next week?" had no antecedent, and there was no record of what the
-- assistant had told a student — which matters when the subject is deadlines.
-- ============================================================================

CREATE TABLE IF NOT EXISTS conversations (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    student_id  UUID NOT NULL REFERENCES students(id) ON DELETE CASCADE,

    -- Generated from the first user message rather than asked for; nobody names
    -- a conversation before having it.
    title       TEXT NOT NULL DEFAULT 'New chat',
    archived    BOOLEAN NOT NULL DEFAULT FALSE,

    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    -- Bumped on every message so the sidebar sorts by recent activity, not by
    -- creation date.
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS conversations_student_recent_idx
    ON conversations (student_id, updated_at DESC)
    WHERE archived = FALSE;


CREATE TABLE IF NOT EXISTS messages (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    conversation_id UUID NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,

    role            TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
    content         TEXT NOT NULL DEFAULT '',

    -- The items this answer was grounded in, denormalised. Kept as a snapshot
    -- rather than as foreign keys: a citation must still render after the item
    -- it referenced has been deleted, and it must show what was said at the
    -- time, not what the row says today.
    sources         JSONB NOT NULL DEFAULT '[]',

    model           TEXT,
    -- NULL until the stream finishes; a row that never completes is how an
    -- interrupted or failed generation is distinguished from a real answer.
    completed_at    TIMESTAMPTZ,
    error           TEXT,

    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS messages_conversation_idx
    ON messages (conversation_id, created_at);


-- Keep conversations.updated_at in step with its newest message, so ordering
-- the sidebar never needs a join or an aggregate.
CREATE OR REPLACE FUNCTION touch_conversation()
RETURNS TRIGGER AS $fn$
BEGIN
    UPDATE conversations SET updated_at = now() WHERE id = NEW.conversation_id;
    RETURN NEW;
END;
$fn$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS messages_touch_conversation ON messages;
CREATE TRIGGER messages_touch_conversation
    AFTER INSERT ON messages
    FOR EACH ROW EXECUTE FUNCTION touch_conversation();
