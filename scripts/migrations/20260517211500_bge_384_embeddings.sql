-- Migration: bge_384_embeddings
-- Applied: 2026-05-17
-- Switch pgvector storage from 768-dim Ollama embeddings to 384-dim BGE embeddings.
--
-- This intentionally invalidates existing vectors. Re-ingest documents after applying.
-- Do not ALTER the vector typmod in place; drop/recreate the column instead.

DROP INDEX IF EXISTS idx_chunk_embeddings_hnsw;

ALTER TABLE chunk_embeddings
    DROP COLUMN IF EXISTS embedding;

ALTER TABLE chunk_embeddings
    ADD COLUMN embedding extensions.vector(384);

CREATE INDEX IF NOT EXISTS idx_chunk_embeddings_hnsw
    ON chunk_embeddings USING hnsw (embedding extensions.vector_cosine_ops);
