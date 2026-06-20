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

from universal_rag.config import get_settings
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


def reciprocal_rank_fusion(
    ranked_id_lists: list[list[int]], *, k: int = 60, weights: list[float] | None = None
) -> dict[int, float]:
    """Weighted RRF: list i contributes weights[i]/(k + rank) per item (weight 1.0
    when unspecified). Higher score = better."""
    scores: dict[int, float] = {}
    for idx, ids in enumerate(ranked_id_lists):
        w = weights[idx] if weights is not None else 1.0
        for rank, cid in enumerate(ids):
            scores[cid] = scores.get(cid, 0.0) + w / (k + rank + 1)
    return scores


class HybridRetriever:
    def __init__(
        self,
        top_k: int | None = None,
        candidate_k: int | None = None,
        *,
        rrf_k: int = 60,
        dense_weight: float | None = None,
        sparse_weight: float | None = None,
        embedder: OllamaEmbedder | None = None,
        reranker: Reranker | None = None,
    ) -> None:
        s = get_settings()
        self.top_k = s.retrieval_top_k if top_k is None else top_k
        self.candidate_k = s.retrieval_candidate_k if candidate_k is None else candidate_k
        self.rrf_k = rrf_k
        self.dense_weight = s.rrf_dense_weight if dense_weight is None else dense_weight
        self.sparse_weight = s.rrf_sparse_weight if sparse_weight is None else sparse_weight
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
        fused = reciprocal_rank_fusion(
            [[r.id for r in sem], [r.id for r in kw]],
            k=self.rrf_k,
            weights=[self.dense_weight, self.sparse_weight],
        )

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
