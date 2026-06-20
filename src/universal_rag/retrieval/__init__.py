"""Project-scoped hybrid retrieval (pgvector + Postgres FTS) with reranking."""

from universal_rag.retrieval.hybrid import (
    HybridRetriever,
    RetrievedChunk,
    reciprocal_rank_fusion,
)
from universal_rag.retrieval.rerank import (
    CrossEncoderReranker,
    NoopReranker,
    OllamaReranker,
    Reranker,
    get_reranker,
)

__all__ = [
    "CrossEncoderReranker",
    "HybridRetriever",
    "NoopReranker",
    "OllamaReranker",
    "Reranker",
    "RetrievedChunk",
    "get_reranker",
    "reciprocal_rank_fusion",
]
