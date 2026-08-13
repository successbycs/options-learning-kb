-- Options Learning KB application schema. Apply only through the reviewed
-- cs-ai-lab-infra PostgreSQL + pgvector adapter on the T480.
CREATE EXTENSION IF NOT EXISTS vector;
CREATE SCHEMA IF NOT EXISTS options_learning_kb;
SET search_path TO options_learning_kb, public;

CREATE TABLE IF NOT EXISTS sources (
    id UUID PRIMARY KEY,
    source_filename TEXT NOT NULL,
    source_sha256 CHAR(64) NOT NULL UNIQUE,
    transcript_sha256 CHAR(64) NOT NULL,
    lesson_title TEXT NOT NULL,
    owner TEXT NOT NULL,
    permission_basis TEXT NOT NULL,
    transcript_review_status TEXT NOT NULL CHECK (transcript_review_status IN ('DRAFT', 'APPROVED', 'DISABLED')),
    review_status TEXT NOT NULL CHECK (review_status IN ('DRAFT', 'APPROVED', 'DISABLED')),
    citation_policy TEXT NOT NULL DEFAULT 'Private learning/research only; do not redistribute.',
    transcript_markdown TEXT NOT NULL,
    manifest JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    approved_at TIMESTAMPTZ,
    disabled_at TIMESTAMPTZ,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CHECK (source_sha256 ~ '^[0-9a-f]{64}$'),
    CHECK (transcript_sha256 ~ '^[0-9a-f]{64}$')
);

CREATE TABLE IF NOT EXISTS documents (
    id UUID PRIMARY KEY,
    source_id UUID NOT NULL REFERENCES sources(id) ON DELETE RESTRICT,
    document_sha256 CHAR(64) NOT NULL,
    filename TEXT NOT NULL,
    lesson_title TEXT NOT NULL,
    content_markdown TEXT NOT NULL,
    is_current BOOLEAN NOT NULL DEFAULT true,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (source_id, document_sha256)
);

CREATE TABLE IF NOT EXISTS ingest_runs (
    id UUID PRIMARY KEY,
    idempotency_key TEXT NOT NULL UNIQUE,
    source_id UUID NOT NULL REFERENCES sources(id) ON DELETE RESTRICT,
    document_id UUID REFERENCES documents(id) ON DELETE RESTRICT,
    input_sha256 CHAR(64) NOT NULL,
    embedding_model TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('STARTED', 'SUCCEEDED', 'SKIPPED', 'FAILED', 'BLOCKED')),
    document_count INTEGER NOT NULL DEFAULT 0,
    chunk_count INTEGER NOT NULL DEFAULT 0,
    error_summary TEXT,
    started_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS chunks (
    id UUID PRIMARY KEY,
    source_id UUID NOT NULL REFERENCES sources(id) ON DELETE RESTRICT,
    document_id UUID NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    ordinal INTEGER NOT NULL CHECK (ordinal >= 0),
    timestamp_start_seconds INTEGER NOT NULL CHECK (timestamp_start_seconds >= 0),
    timestamp_start_label TEXT NOT NULL,
    timestamp_end_seconds INTEGER,
    timestamp_end_label TEXT,
    chunk_sha256 CHAR(64) NOT NULL,
    passage TEXT NOT NULL,
    embedding_model TEXT NOT NULL,
    embedding VECTOR(1024) NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (document_id, ordinal),
    UNIQUE (document_id, chunk_sha256)
);

CREATE INDEX IF NOT EXISTS idx_olkb_documents_source ON documents(source_id);
CREATE INDEX IF NOT EXISTS idx_olkb_chunks_source ON chunks(source_id);
CREATE INDEX IF NOT EXISTS idx_olkb_chunks_document ON chunks(document_id);
CREATE INDEX IF NOT EXISTS idx_olkb_sources_status ON sources(review_status);
CREATE INDEX IF NOT EXISTS idx_olkb_chunks_embedding_hnsw
    ON chunks USING hnsw (embedding vector_cosine_ops);

CREATE TABLE IF NOT EXISTS retrieval_qa_questions (
    id UUID PRIMARY KEY,
    question TEXT NOT NULL,
    expected_source_id UUID REFERENCES sources(id) ON DELETE SET NULL,
    expected_timestamp_seconds INTEGER,
    expected_timestamp_label TEXT,
    active BOOLEAN NOT NULL DEFAULT true,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS retrieval_qa_runs (
    id UUID PRIMARY KEY,
    qa_question_id UUID NOT NULL REFERENCES retrieval_qa_questions(id) ON DELETE CASCADE,
    result TEXT NOT NULL CHECK (result IN ('PASS', 'FAIL')),
    top_chunk_id UUID REFERENCES chunks(id) ON DELETE SET NULL,
    expected_source_id UUID,
    expected_timestamp_seconds INTEGER,
    observed_source_id UUID,
    observed_timestamp_seconds INTEGER,
    notes TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS knowledge_gaps (
    id UUID PRIMARY KEY,
    question TEXT NOT NULL,
    source_scope UUID[],
    status TEXT NOT NULL DEFAULT 'OPEN' CHECK (status IN ('OPEN', 'RESOLVED', 'NOT_A_KB_QUESTION')),
    rationale TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    resolved_at TIMESTAMPTZ
);

CREATE OR REPLACE FUNCTION match_approved_chunks(
    query_embedding VECTOR(1024),
    source_scope UUID[] DEFAULT NULL,
    result_limit INTEGER DEFAULT 8
)
RETURNS TABLE (
    chunk_id UUID,
    source_id UUID,
    lesson_title TEXT,
    source_sha256 CHAR(64),
    document_sha256 CHAR(64),
    chunk_sha256 CHAR(64),
    timestamp_label TEXT,
    timestamp_seconds INTEGER,
    passage TEXT,
    similarity DOUBLE PRECISION
)
LANGUAGE sql STABLE AS $$
    SELECT c.id, s.id, s.lesson_title, s.source_sha256, d.document_sha256, c.chunk_sha256, c.timestamp_start_label,
           c.timestamp_start_seconds, c.passage,
           1 - (c.embedding <=> query_embedding) AS similarity
    FROM chunks c
    JOIN documents d ON d.id = c.document_id
    JOIN sources s ON s.id = c.source_id
    WHERE s.review_status = 'APPROVED'
      AND d.is_current = true
      AND (source_scope IS NULL OR c.source_id = ANY(source_scope))
    ORDER BY c.embedding <=> query_embedding
    LIMIT GREATEST(1, LEAST(result_limit, 50));
$$;

REVOKE ALL ON SCHEMA options_learning_kb FROM PUBLIC;
