"""Hybrid retrieval: fuse semantic (pgvector cosine) + keyword (Postgres FTS),
then optionally rerank the fused set.

All queries are scoped by project_id (and optionally source_id / metadata) so a
draft about one project never pulls context from another. Candidates from each
arm are merged with Reciprocal Rank Fusion (RRF), which needs no score
calibration between the two very different scoring scales.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import ColumnElement, func, select
from sqlalchemy.orm import Session

from universal_rag.db.models import Chunk
from universal_rag.db.session import get_session
from universal_rag.embeddings import OllamaEmbedder
from universal_rag.retrieval.rerank import Reranker, get_reranker

# Columns every candidate query returns (kept identical so rows fuse cleanly).
_COLS = (
    Chunk.id,
    Chunk.document_id,
    Chunk.project_id,
    Chunk.source_id,
    Chunk.content,
    Chunk.chunk_metadata,
)


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


def reciprocal_rank_fusion(ranked_id_lists: list[list[int]], *, k: int = 60) -> dict[int, float]:
    """RRF: each list contributes 1/(k + rank) per item. Higher score = better."""
    scores: dict[int, float] = {}
    for ids in ranked_id_lists:
        for rank, cid in enumerate(ids):
            scores[cid] = scores.get(cid, 0.0) + 1.0 / (k + rank + 1)
    return scores


class HybridRetriever:
    def __init__(
        self,
        top_k: int = 12,
        candidate_k: int = 50,
        *,
        rrf_k: int = 60,
        embedder: OllamaEmbedder | None = None,
        reranker: Reranker | None = None,
    ) -> None:
        self.top_k = top_k
        self.candidate_k = candidate_k
        self.rrf_k = rrf_k
        self.embedder = embedder or OllamaEmbedder()
        self.reranker = reranker or get_reranker()

    def _conditions(
        self, project_id: str, source_ids: list[str] | None, filters: Mapping[str, object] | None
    ) -> list[ColumnElement[bool]]:
        conds: list[ColumnElement[bool]] = [Chunk.project_id == project_id]
        if source_ids:
            conds.append(Chunk.source_id.in_(source_ids))
        for key, val in (filters or {}).items():
            conds.append(Chunk.chunk_metadata[key].astext == str(val))
        return conds

    def _semantic_ids(
        self, session: Session, qvec: list[float], conds: list[ColumnElement[bool]]
    ) -> list[Any]:
        dist = Chunk.embedding.cosine_distance(qvec)
        stmt = select(*_COLS).where(*conds).order_by(dist).limit(self.candidate_k)
        return list(session.execute(stmt).all())

    def _keyword_ids(
        self, session: Session, query: str, conds: list[ColumnElement[bool]]
    ) -> list[Any]:
        tsv = func.to_tsvector("english", Chunk.content)
        tsq = func.websearch_to_tsquery("english", query)
        rank = func.ts_rank_cd(tsv, tsq)
        stmt = (
            select(*_COLS)
            .where(*conds, tsv.op("@@")(tsq))
            .order_by(rank.desc())
            .limit(self.candidate_k)
        )
        return list(session.execute(stmt).all())

    def search(
        self,
        query: str,
        project_id: str,
        *,
        source_ids: list[str] | None = None,
        filters: Mapping[str, object] | None = None,
    ) -> list[RetrievedChunk]:
        if not query.strip():
            return []

        qvec = self.embedder.embed_query(query)
        conds = self._conditions(project_id, source_ids, filters)
        with get_session() as session:
            sem = self._semantic_ids(session, qvec, conds)
            kw = self._keyword_ids(session, query, conds)

        rows = {r.id: r for r in (*sem, *kw)}
        fused = reciprocal_rank_fusion([[r.id for r in sem], [r.id for r in kw]], k=self.rrf_k)

        candidates: list[RetrievedChunk] = []
        for cid, score in sorted(fused.items(), key=lambda kv: kv[1], reverse=True)[
            : self.candidate_k
        ]:
            r = rows[cid]
            meta = r.chunk_metadata or {}
            candidates.append(
                RetrievedChunk(
                    chunk_id=r.id,
                    document_id=r.document_id,
                    project_id=r.project_id,
                    source_id=r.source_id,
                    content=r.content,
                    title=str(meta.get("title", "")),
                    url=str(meta.get("url", "")),
                    score=score,
                    metadata=meta,
                )
            )

        return self.reranker.rerank(query, candidates)[: self.top_k]
