"""Import a BEIR-style dataset (corpus + queries + qrels) as a testing corpus.

BEIR ships `corpus` (_id, title, text), `queries` (_id, text), and a sibling
`<name>-qrels` (query-id, corpus-id, score). We ingest the corpus directly as a
project's documents (external_id = corpus _id) using the bulk batched path, then
convert queries+qrels into a golden set whose `relevant_doc_ids` are the qrels —
so retrieval metrics line up with human relevance judgments.

Requires the `datasets` extra (`uv sync --extra dev`).
"""

from __future__ import annotations

from dataclasses import dataclass

from universal_rag.connectors.base import SourceDocument
from universal_rag.db.models import Document, Project, Source
from universal_rag.db.session import get_session
from universal_rag.eval.dataset import GoldenItem
from universal_rag.ingestion.pipeline import IngestionPipeline


@dataclass(slots=True)
class ImportResult:
    project_id: str
    source_id: str
    documents: int
    chunks: int
    golden_items: int


def import_beir(
    dataset: str,
    project_id: str,
    *,
    source_id: str | None = None,
    qrels_splits: tuple[str, ...] = ("test",),
    max_docs: int = 0,
    page: int = 1000,
) -> tuple[ImportResult, list[GoldenItem]]:
    """Ingest `dataset`'s corpus into `project_id` and build a golden set from its
    queries + qrels. `max_docs` > 0 limits corpus ingest (for quick tests)."""
    from datasets import load_dataset

    sid = source_id or f"{project_id}-corpus"
    pipeline = IngestionPipeline()

    # --- ingest corpus (bulk, paged) ---
    with get_session() as session:
        if session.get(Project, project_id) is None:
            session.add(Project(id=project_id, name=f"BEIR {dataset}", description=dataset))
        if session.get(Source, sid) is None:
            session.add(
                Source(id=sid, project_id=project_id, provider="beir", config={"dataset": dataset})
            )

    corpus = load_dataset(dataset, "corpus", split="corpus")
    total_docs = total_chunks = 0
    batch: list[SourceDocument] = []

    def _flush(docs: list[SourceDocument]) -> None:
        nonlocal total_docs, total_chunks
        if not docs:
            return
        with get_session() as session:
            d, c = pipeline.bulk_index(session, project_id, sid, docs)
        total_docs += d
        total_chunks += c

    for i, row in enumerate(corpus):
        if max_docs and i >= max_docs:
            break
        text = (row.get("text") or "").strip()
        if not text:
            continue
        batch.append(
            SourceDocument(
                source_id=sid,
                provider="beir",
                external_id=str(row["_id"]),
                title=row.get("title") or "",
                content=text,
                metadata={"doc_type": "beir", "dataset": dataset},
            )
        )
        if len(batch) >= page:
            _flush(batch)
            batch = []
    _flush(batch)

    # --- build golden from queries + qrels ---
    queries = {str(r["_id"]): r["text"] for r in load_dataset(dataset, "queries", split="queries")}
    qrels = load_dataset(f"{dataset}-qrels")
    ingested = _ingested_doc_ids(sid)

    golden: list[GoldenItem] = []
    for split in qrels_splits:
        relevant: dict[str, list[str]] = {}
        for r in qrels[split]:
            if int(r["score"]) > 0:
                relevant.setdefault(str(r["query-id"]), []).append(str(r["corpus-id"]))
        for qid, doc_ids in relevant.items():
            query = queries.get(qid)
            # keep only docs we actually ingested (matters when max_docs is set)
            doc_ids = [d for d in doc_ids if d in ingested]
            if query and doc_ids:
                golden.append(
                    GoldenItem(query=query, project_id=project_id, relevant_doc_ids=doc_ids)
                )

    result = ImportResult(project_id, sid, total_docs, total_chunks, len(golden))
    return result, golden


def _ingested_doc_ids(source_id: str) -> set[str]:
    from sqlalchemy import select

    with get_session() as session:
        return set(
            session.scalars(select(Document.external_id).where(Document.source_id == source_id))
        )
