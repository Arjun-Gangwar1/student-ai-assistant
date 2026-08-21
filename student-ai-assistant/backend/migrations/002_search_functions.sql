-- ============================================================================
-- 002_search_functions — retrieval for the RAG layer
--
-- The previous implementation ran two queries from Python and merged them in
-- application code. The keyword half passed the raw user question to
-- to_tsquery(), which rejects unquoted spaces and punctuation, so every
-- natural-language question threw and was swallowed by a try/except —
-- "hybrid" search had silently been semantic-only since it was written.
--
-- Both halves now run in one SQL statement, fused with Reciprocal Rank Fusion,
-- and the keyword half uses websearch_to_tsquery(), which is built to accept
-- exactly the kind of string a human types.
-- ============================================================================

-- ─── Semantic-only search (kept: used by dedup and by tests) ─────────────────
DROP FUNCTION IF EXISTS match_items(vector, uuid, int, float);
DROP FUNCTION IF EXISTS match_items(vector(768), uuid, int, float);

CREATE OR REPLACE FUNCTION match_items(
    query_embedding      vector(768),
    match_student_ids    UUID[],
    match_count          INT   DEFAULT 10,
    similarity_threshold REAL  DEFAULT 0.3
)
RETURNS TABLE (
    id              UUID,
    student_id      UUID,
    source          TEXT,
    source_id       TEXT,
    title           TEXT,
    summary         TEXT,
    raw_content     TEXT,
    category        TEXT,
    priority        TEXT,
    relevance_score REAL,
    confidence      REAL,
    deadline        TIMESTAMPTZ,
    is_read         BOOLEAN,
    metadata        JSONB,
    created_at      TIMESTAMPTZ,
    similarity      REAL
)
LANGUAGE sql STABLE
AS $$
    SELECT
        i.id, i.student_id, i.source, i.source_id, i.title, i.summary,
        i.raw_content, i.category, i.priority, i.relevance_score, i.confidence,
        i.deadline, i.is_read, i.metadata, i.created_at,
        (1 - (i.embedding <=> query_embedding))::REAL AS similarity
    FROM items i
    WHERE i.student_id = ANY (match_student_ids)
      AND i.embedding IS NOT NULL
      AND (1 - (i.embedding <=> query_embedding)) > similarity_threshold
    ORDER BY i.embedding <=> query_embedding
    LIMIT match_count;
$$;


-- ─── Hybrid search: vector + full-text, fused with RRF ───────────────────────
--
-- Reciprocal Rank Fusion scores each document as sum(1 / (k + rank_i)) across
-- result lists. It combines rankings whose scores are not comparable — cosine
-- similarity and ts_rank live on different scales — without needing to
-- normalise either. k=60 is the value from the original RRF paper and is a
-- sane default; it damps the influence of any single list's top hit.
--
DROP FUNCTION IF EXISTS hybrid_search_items(vector, text, uuid[], int, real, real);

CREATE OR REPLACE FUNCTION hybrid_search_items(
    query_embedding   vector(768),
    query_text        TEXT,
    match_student_ids UUID[],
    match_count       INT  DEFAULT 10,
    semantic_weight   REAL DEFAULT 1.0,
    keyword_weight    REAL DEFAULT 1.0,
    rrf_k             INT  DEFAULT 60
)
RETURNS TABLE (
    id              UUID,
    student_id      UUID,
    source          TEXT,
    source_id       TEXT,
    title           TEXT,
    summary         TEXT,
    raw_content     TEXT,
    category        TEXT,
    priority        TEXT,
    relevance_score REAL,
    confidence      REAL,
    deadline        TIMESTAMPTZ,
    is_read         BOOLEAN,
    metadata        JSONB,
    created_at      TIMESTAMPTZ,
    semantic_rank   INT,
    keyword_rank    INT,
    rrf_score       REAL
)
LANGUAGE sql STABLE
AS $$
WITH
-- websearch_to_tsquery accepts free-form input: quoted phrases, OR, leading -.
-- It never raises on punctuation, unlike to_tsquery.
--
-- It also ANDs every term, which is right for a search box but too strict for a
-- conversational question. "what assignments are due this week?" becomes
-- 'assign' & 'due' & 'week', and a document titled "Assignment 3 ... due Friday"
-- fails on 'week' alone — the keyword half contributes nothing exactly when the
-- student phrases a natural question.
--
-- So: try the strict AND form, and fall back to an OR-relaxed form only when
-- AND matches nothing. Precision when it is available, recall when it is not.
-- ts_rank_cd still ranks documents matching more terms above those matching one.
tsq_both AS (
    SELECT
        websearch_to_tsquery('english', coalesce(query_text, '')) AS q_and,
        NULLIF(
            replace(
                websearch_to_tsquery('english', coalesce(query_text, ''))::text,
                ' & ', ' | '
            ),
            ''
        )::tsquery AS q_or
),
tsq AS (
    SELECT CASE
        WHEN b.q_and IS NULL OR b.q_and = ''::tsquery THEN NULL
        WHEN EXISTS (
            SELECT 1 FROM items i
            WHERE i.student_id = ANY (match_student_ids)
              AND i.search_vector @@ b.q_and
        ) THEN b.q_and
        ELSE b.q_or
    END AS q
    FROM tsq_both b
),
semantic AS (
    SELECT i.id,
           ROW_NUMBER() OVER (ORDER BY i.embedding <=> query_embedding)::INT AS rank
    FROM items i
    WHERE i.student_id = ANY (match_student_ids)
      AND i.embedding IS NOT NULL
      AND query_embedding IS NOT NULL
    ORDER BY i.embedding <=> query_embedding
    LIMIT match_count * 4
),
keyword AS (
    SELECT i.id,
           ROW_NUMBER() OVER (
               ORDER BY ts_rank_cd(i.search_vector, tsq.q) DESC, i.created_at DESC
           )::INT AS rank
    FROM items i, tsq
    WHERE i.student_id = ANY (match_student_ids)
      AND tsq.q IS NOT NULL
      AND tsq.q != ''::tsquery
      AND i.search_vector @@ tsq.q
    ORDER BY ts_rank_cd(i.search_vector, tsq.q) DESC, i.created_at DESC
    LIMIT match_count * 4
),
fused AS (
    SELECT
        COALESCE(s.id, k.id) AS id,
        s.rank AS semantic_rank,
        k.rank AS keyword_rank,
        (
            COALESCE(semantic_weight / (rrf_k + s.rank), 0) +
            COALESCE(keyword_weight  / (rrf_k + k.rank), 0)
        )::REAL AS rrf_score
    FROM semantic s
    FULL OUTER JOIN keyword k ON s.id = k.id
)
SELECT
    i.id, i.student_id, i.source, i.source_id, i.title, i.summary,
    i.raw_content, i.category, i.priority, i.relevance_score, i.confidence,
    i.deadline, i.is_read, i.metadata, i.created_at,
    f.semantic_rank, f.keyword_rank, f.rrf_score
FROM fused f
JOIN items i ON i.id = f.id
ORDER BY f.rrf_score DESC
LIMIT match_count;
$$;


-- ─── Near-duplicate detection ────────────────────────────────────────────────
-- Campus notices arrive repeatedly: the same circular by email, on the portal,
-- and in a Classroom announcement. Feature 14 in the plan ("duplicate
-- announcement deduplication") needs this.
CREATE OR REPLACE FUNCTION find_similar_items(
    target_item_id UUID,
    threshold      REAL DEFAULT 0.92,
    max_results    INT  DEFAULT 5
)
RETURNS TABLE (id UUID, title TEXT, source TEXT, similarity REAL)
LANGUAGE sql STABLE
AS $$
    SELECT o.id, o.title, o.source,
           (1 - (o.embedding <=> t.embedding))::REAL AS similarity
    FROM items t
    JOIN items o
      ON o.student_id = t.student_id
     AND o.id <> t.id
     AND o.embedding IS NOT NULL
    WHERE t.id = target_item_id
      AND t.embedding IS NOT NULL
      AND (1 - (o.embedding <=> t.embedding)) >= threshold
    ORDER BY o.embedding <=> t.embedding
    LIMIT max_results;
$$;
