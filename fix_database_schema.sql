-- Fix database schema to use 384 dimensions instead of 1536
-- Run this in Supabase SQL Editor

-- First, drop existing functions
DROP FUNCTION IF EXISTS search_document_chunks(vector(1536), uuid, int);
DROP FUNCTION IF EXISTS get_document_context(vector(1536), uuid[], uuid, int);

-- Drop existing table if it exists and recreate with correct dimensions
-- WARNING: This will delete existing data - only run on development/test databases
DROP TABLE IF EXISTS document_chunks;

-- Create document_chunks table with 384 dimensions
CREATE TABLE document_chunks (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    document_id uuid NOT NULL,
    user_id uuid NOT NULL,
    chunk_text text NOT NULL,
    chunk_index integer NOT NULL,
    chunk_metadata jsonb DEFAULT '{}',
    embedding vector(384), -- 384 dimensions for all-MiniLM-L6-v2
    created_at timestamp with time zone DEFAULT timezone('utc'::text, now()) NOT NULL
);

-- Create indexes for performance
CREATE INDEX idx_document_chunks_document_id ON document_chunks(document_id);
CREATE INDEX idx_document_chunks_user_id ON document_chunks(user_id);
CREATE INDEX idx_document_chunks_embedding ON document_chunks USING ivfflat (embedding vector_cosine_ops);

-- Enable Row Level Security
ALTER TABLE document_chunks ENABLE ROW LEVEL SECURITY;

-- Create RLS policy
CREATE POLICY "Users can only access their own document chunks" ON document_chunks
    FOR ALL USING (auth.uid() = user_id);

-- Create the search function with 384 dimensions
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

-- Create the document context function with 384 dimensions
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

-- Grant permissions
GRANT EXECUTE ON FUNCTION search_document_chunks(vector(384), uuid, int) TO authenticated;
GRANT EXECUTE ON FUNCTION get_document_context(vector(384), uuid[], uuid, int) TO authenticated;

-- Also ensure the documents table exists with proper structure
CREATE TABLE IF NOT EXISTS documents (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id uuid NOT NULL,
    filename text NOT NULL,
    file_type text NOT NULL,
    status text NOT NULL DEFAULT 'pending',
    file_size bigint DEFAULT 0,
    storage_path text,
    chunk_count integer DEFAULT 0,
    error_message text,
    created_at timestamp with time zone DEFAULT timezone('utc'::text, now()) NOT NULL,
    processing_started_at timestamp with time zone,
    processed_at timestamp with time zone
);

-- Create indexes for documents table
CREATE INDEX IF NOT EXISTS idx_documents_user_id ON documents(user_id);
CREATE INDEX IF NOT EXISTS idx_documents_status ON documents(status);

-- Enable RLS for documents table
ALTER TABLE documents ENABLE ROW LEVEL SECURITY;

-- Create RLS policy for documents
DROP POLICY IF EXISTS "Users can only access their own documents" ON documents;
CREATE POLICY "Users can only access their own documents" ON documents
    FOR ALL USING (auth.uid() = user_id);

-- Grant table permissions
GRANT ALL ON documents TO authenticated;
GRANT ALL ON document_chunks TO authenticated;