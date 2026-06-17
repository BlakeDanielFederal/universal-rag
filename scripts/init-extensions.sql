-- Runs once on first DB init (docker-entrypoint-initdb.d).
-- pgvector for embeddings; pg_trgm helps the keyword side of hybrid search.
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pg_trgm;
