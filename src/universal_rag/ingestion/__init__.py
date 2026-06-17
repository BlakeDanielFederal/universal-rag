"""Ingestion pipeline: connector docs -> chunks -> embeddings -> pgvector.

Orchestrates incremental sync per source:
  1. load SyncState cursor
  2. connector.fetch(cursor) -> SourceDocuments
  3. skip unchanged (content_hash), chunk changed docs
  4. embed chunks (OllamaEmbedder), upsert Document+Chunks
  5. advance + persist the cursor
"""

from universal_rag.ingestion.pipeline import IngestionPipeline, sync_source

__all__ = ["IngestionPipeline", "sync_source"]
