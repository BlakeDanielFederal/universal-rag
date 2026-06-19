"""Drive the retriever over a golden set, aggregate metrics, gate on a baseline."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from universal_rag.eval import metrics as M
from universal_rag.eval.dataset import GoldenItem

if TYPE_CHECKING:
    from universal_rag.eval.judge import Judge
    from universal_rag.retrieval import HybridRetriever

_KS = (5, 10, 20)


@dataclass(slots=True)
class EvalReport:
    n: int
    metrics: dict[str, float]


def _relevant_chunk_ids(item: GoldenItem) -> set[int]:
    """Resolve the golden item's relevant documents to their CURRENT chunk ids."""
    from sqlalchemy import select

    from universal_rag.db.models import Chunk, Document
    from universal_rag.db.session import get_session

    if not item.relevant_doc_ids:
        return set()
    with get_session() as session:
        return set(
            session.scalars(
                select(Chunk.id)
                .join(Document, Chunk.document_id == Document.id)
                .where(
                    Document.project_id == item.project_id,
                    Document.external_id.in_(item.relevant_doc_ids),
                )
            )
        )


def run_eval(
    golden: list[GoldenItem],
    *,
    retriever: HybridRetriever | None = None,
    judge: Judge | None = None,
    ks: tuple[int, ...] = _KS,
    top_k: int = 20,
) -> EvalReport:
    from universal_rag.retrieval import HybridRetriever

    retriever = retriever or HybridRetriever(top_k=top_k)
    agg: dict[str, list[float]] = {}

    def add(key: str, value: float) -> None:
        agg.setdefault(key, []).append(value)

    for item in golden:
        hits = retriever.search(item.query, item.project_id, source_ids=item.source_ids)
        retrieved = [h.chunk_id for h in hits]
        relevant = _relevant_chunk_ids(item)
        for k in ks:
            add(f"recall@{k}", M.recall_at_k(retrieved, relevant, k))
            add(f"ndcg@{k}", M.ndcg_at_k(retrieved, relevant, k))
        add("mrr", M.reciprocal_rank(retrieved, relevant))
        if judge is not None:
            add(
                "context_support",
                judge.context_support(item.query, item.ideal_answer, [h.content for h in hits]),
            )

    aggregated = {k: (sum(v) / len(v) if v else 0.0) for k, v in agg.items()}
    return EvalReport(n=len(golden), metrics=aggregated)


def compare_baseline(
    report: EvalReport, baseline: dict[str, float], *, tol: float = 0.01
) -> list[str]:
    """Return human-readable regressions (a metric dropped > tol below baseline)."""
    regressions: list[str] = []
    for key, base in baseline.items():
        cur = report.metrics.get(key)
        if cur is not None and cur < base - tol:
            regressions.append(f"{key}: {cur:.4f} < baseline {base:.4f}")
    return regressions
