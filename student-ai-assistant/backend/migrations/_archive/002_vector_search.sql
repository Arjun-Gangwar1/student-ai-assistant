-- Supabase RPC function for semantic vector search.
-- Called by the RAG retriever: db.rpc("match_items", {...})

CREATE OR REPLACE FUNCTION match_items(
    query_embedding vector(768),
    student_id_filter uuid,
    match_count int DEFAULT 10
)
RETURNS TABLE (
    id uuid,
    title text,
    summary text,
    source text,
    category text,
    priority text,
    relevance_score float,
    deadline timestamptz,
    is_read bool,
    created_at timestamptz,
    similarity float
)
LANGUAGE plpgsql
AS $$
BEGIN
    RETURN QUERY
    SELECT
        i.id,
        i.title,
        i.summary,
        i.source,
        i.category,
        i.priority,
        i.relevance_score,
        i.deadline,
        i.is_read,
        i.created_at,
        1 - (i.embedding <=> query_embedding) AS similarity
    FROM items i
    WHERE
        i.student_id = student_id_filter
        AND i.embedding IS NOT NULL
    ORDER BY i.embedding <=> query_embedding
    LIMIT match_count;
END;
$$;
