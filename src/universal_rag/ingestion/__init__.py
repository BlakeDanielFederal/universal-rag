"""Ingestion pipeline: connector docs -> chunks -> embeddings -> pgvector.

Orchestrates incremental sync per source:
  1. seed Project/Source rows from config
  2. load SyncState cursor
  3. connector.fetch(cursor) -> SourceDocuments
  4. skip unchanged (content_hash), chunk changed docs
  5. embed chunks (OllamaEmbedder), upsert Document+Chunks
  6. advance + persist the cursor
"""

from universal_rag.ingestion.pipeline import (
    IngestionPipeline,
    ReindexResult,
    SourceSyncResult,
    reindex,
    run_sync,
)

__all__ = ["IngestionPipeline", "ReindexResult", "SourceSyncResult", "reindex", "run_sync"]
