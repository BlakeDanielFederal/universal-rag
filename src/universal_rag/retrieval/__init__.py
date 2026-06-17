"""Project-scoped hybrid retrieval (pgvector + Postgres FTS) with reranking."""

from universal_rag.retrieval.hybrid import HybridRetriever, RetrievedChunk

__all__ = ["HybridRetriever", "RetrievedChunk"]
