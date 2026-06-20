"""bulk_index: batched cross-document ingest (used by the BEIR importer)."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from sqlalchemy import func, select

from universal_rag.connectors.base import SourceDocument
from universal_rag.db.models import Chunk, Document, Project, Source
from universal_rag.db.session import get_engine, get_session
from universal_rag.ingestion.pipeline import IngestionPipeline

PROJECT_ID = "itest_bulk"
SOURCE_ID = f"{PROJECT_ID}-corpus"


def _services_up() -> bool:
    try:
        get_engine().connect().close()
        from universal_rag.embeddings import OllamaEmbedder

        OllamaEmbedder().embed(["ping"])
        return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(not _services_up(), reason="Postgres/Ollama not available")


@pytest.fixture(autouse=True)
def _cleanup() -> Iterator[None]:
    yield
    with get_session() as s:
        proj = s.get(Project, PROJECT_ID)
        if proj is not None:
            s.delete(proj)


def test_bulk_index_inserts_docs_and_chunks() -> None:
    docs = [
        SourceDocument(
            source_id=SOURCE_ID,
            provider="beir",
            external_id=str(i),
            title=f"Doc {i}",
            content=f"Document number {i} about analytics and revenue dashboards. " * 4,
            metadata={"dataset": "test"},
        )
        for i in range(5)
    ]
    pipeline = IngestionPipeline()
    with get_session() as s:
        s.add(Project(id=PROJECT_ID, name="bulk"))
        s.add(Source(id=SOURCE_ID, project_id=PROJECT_ID, provider="beir", config={}))
        n_docs, n_chunks = pipeline.bulk_index(s, PROJECT_ID, SOURCE_ID, docs, embed_batch=3)

    assert n_docs == 5
    assert n_chunks >= 5
    with get_session() as s:
        assert (
            s.scalar(
                select(func.count()).select_from(Document).where(Document.source_id == SOURCE_ID)
            )
            == 5
        )
        chunk_rows = s.scalars(select(Chunk).where(Chunk.source_id == SOURCE_ID)).all()
        assert len(chunk_rows) == n_chunks
        assert all(len(list(c.embedding)) == 768 for c in chunk_rows)
        assert all(c.embedding_model == "nomic-embed-text" for c in chunk_rows)
        # body stored, signature stamped on the document
        doc0 = s.scalars(select(Document).where(Document.external_id == "0")).one()
        assert doc0.body.startswith("Document number 0")
        assert doc0.chunk_scheme == "sliding-512-64"
