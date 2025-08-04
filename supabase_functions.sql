-- Vector similarity search function for Supabase
-- Run this in Supabase SQL Editor after creating the tables

CREATE OR REPLACE FUNCTION search_document_chunks(
    query_embedding vector(384),
    user_id uuid,
    match_count int DEFAULT 5
) RETURNS TABLE (
    id uuid,
    document_id uuid,
    chunk_text text,
    chunk_index integer,
    chunk_metadata jsonb,
    similarity float
) LANGUAGE plpgsql AS $$
BEGIN
    RETURN QUERY
    SELECT 
        dc.id,
        dc.document_id,
        dc.chunk_text,
        dc.chunk_index,
        dc.chunk_metadata,
        1 - (dc.embedding <=> query_embedding) AS similarity
    FROM document_chunks dc
    WHERE dc.user_id = $2
        AND dc.embedding IS NOT NULL
    ORDER BY dc.embedding <=> query_embedding
    LIMIT match_count;
END;
$$;

-- Grant execute permission to authenticated users
GRANT EXECUTE ON FUNCTION search_document_chunks(vector(384), uuid, int) TO authenticated;

-- Create an index on user_id and embedding for better performance
CREATE INDEX IF NOT EXISTS idx_document_chunks_user_embedding 
ON document_chunks(user_id) 
INCLUDE (embedding);

-- Optional: Create a function to get document context for RAG
CREATE OR REPLACE FUNCTION get_document_context(
    query_embedding vector(384),
    document_ids uuid[],
    user_id uuid,
    match_count int DEFAULT 5
) RETURNS TABLE (
    id uuid,
    document_id uuid,
    chunk_text text,
    chunk_index integer,
    document_filename text,
    similarity float
) LANGUAGE plpgsql AS $$
BEGIN
    RETURN QUERY
    SELECT 
        dc.id,
        dc.document_id,
        dc.chunk_text,
        dc.chunk_index,
        d.filename as document_filename,
        1 - (dc.embedding <=> query_embedding) AS similarity
    FROM document_chunks dc
    JOIN documents d ON dc.document_id = d.id
    WHERE dc.user_id = $3
        AND dc.document_id = ANY(document_ids)
        AND dc.embedding IS NOT NULL
        AND d.status = 'completed'
    ORDER BY dc.embedding <=> query_embedding
    LIMIT match_count;
END;
$$;

-- Grant execute permission
GRANT EXECUTE ON FUNCTION get_document_context(vector(384), uuid[], uuid, int) TO authenticated;