"""Hybrid retrieval: fuse semantic (pgvector cosine) + keyword (Postgres FTS),
then optionally rerank the fused set with a local cross-encoder / Ollama model.

All queries are scoped by project_id (and optionally source_id / metadata) so a
draft about one project never pulls context from another.

TODO(impl):
  - semantic: ORDER BY embedding <=> :qvec  (HNSW index)
  - keyword:  websearch_to_tsquery over to_tsvector('english', content)
  - fuse via Reciprocal Rank Fusion, dedupe by document
  - rerank top-K with RERANK_MODEL when set
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(slots=True)
class RetrievedChunk:
    chunk_id: int
    document_id: int
    project_id: str
    source_id: str
    content: str
    title: str
    url: str
    score: float
    metadata: dict[str, object] = field(default_factory=dict)


class HybridRetriever:
    def __init__(self, top_k: int = 12, candidate_k: int = 50) -> None:
        self.top_k = top_k
        self.candidate_k = candidate_k

    def search(
        self,
        query: str,
        project_id: str,
        *,
        source_ids: list[str] | None = None,
        filters: dict[str, object] | None = None,
    ) -> list[RetrievedChunk]:
        raise NotImplementedError("HybridRetriever.search not yet implemented")
