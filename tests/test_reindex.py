"""Versioning foundation: body storage, signature staleness, reindex (live)."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime

import pytest
from sqlalchemy import func, select

from universal_rag.config.schema import (
    AppConfig,
    ChunkConfig,
    Defaults,
    ProjectConfig,
    SourceConfig,
)
from universal_rag.connectors.base import Connector, SourceDocument, SyncCursor
from universal_rag.db.models import Chunk, Document, Project
from universal_rag.db.session import get_engine, get_session

PROJECT_ID = "itest_reidx"
SOURCE_ID = f"{PROJECT_ID}-confluence"
_BODY = "Apollo analytics dashboard plan. " * 60  # ~360 words → multi-chunk at small sizes


def _services_up() -> bool:
    try:
        get_engine().connect().close()
        from universal_rag.embeddings import OllamaEmbedder

        OllamaEmbedder().embed(["ping"])
        return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(not _services_up(), reason="Postgres/Ollama not available")


class _Fake(Connector):
    provider = "confluence"

    def fetch(self, cursor: SyncCursor | None = None) -> Iterator[SourceDocument]:
        yield SourceDocument(
            source_id=self.source.id,
            provider="confluence",
            external_id="1",
            title="Doc 1",
            content=_BODY,
            updated_at=datetime(2024, 3, 1, tzinfo=UTC),
            metadata={"space": "APOLLO"},
        )


def _config(*, max_tokens: int = 512, store_body: bool = True) -> AppConfig:
    source = SourceConfig(
        id=SOURCE_ID, provider="confluence", spaces=["APOLLO"], store_body=store_body
    )
    return AppConfig(
        defaults=Defaults(chunk=ChunkConfig(max_tokens=max_tokens, overlap_tokens=16)),
        projects=[ProjectConfig(id=PROJECT_ID, name="R", sources=[source])],
    )


@pytest.fixture(autouse=True)
def _connector(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setattr("universal_rag.ingestion.pipeline.get_connector", lambda s: _Fake(s))
    yield
    with get_session() as s:
        proj = s.get(Project, PROJECT_ID)
        if proj is not None:
            s.delete(proj)


def _chunk_count() -> int:
    with get_session() as s:
        return (
            s.scalar(select(func.count()).select_from(Chunk).where(Chunk.source_id == SOURCE_ID))
            or 0
        )


def _doc() -> Document:
    with get_session() as s:
        return s.scalars(select(Document).where(Document.source_id == SOURCE_ID)).one()


def test_body_and_signature_stored_then_skipped() -> None:
    from universal_rag.ingestion import run_sync

    r = run_sync(PROJECT_ID, config=_config())[0]
    assert r.documents == 1
    doc = _doc()
    assert doc.body == _BODY  # raw body persisted
    assert doc.embedding_model == "nomic-embed-text"
    assert doc.chunk_scheme == "sliding-512-16"
    with get_session() as s:
        ch = s.scalars(select(Chunk).where(Chunk.source_id == SOURCE_ID)).first()
        assert ch.embedding_model == "nomic-embed-text" and ch.chunk_scheme == "sliding-512-16"

    # Same config again → unchanged content + signature → skipped.
    again = run_sync(PROJECT_ID, config=_config())[0]
    assert again.skipped == 1 and again.documents == 0


def test_chunk_scheme_change_forces_reembed() -> None:
    from universal_rag.ingestion import run_sync

    run_sync(PROJECT_ID, config=_config(max_tokens=512))
    big = _chunk_count()
    # Smaller chunks → different scheme → re-written (not skipped), more chunks.
    r = run_sync(PROJECT_ID, config=_config(max_tokens=40))[0]
    assert r.documents == 1 and r.skipped == 0
    assert _doc().chunk_scheme == "sliding-40-16"
    assert _chunk_count() > big


def test_reindex_rebuilds_from_body_without_connector() -> None:
    from universal_rag.ingestion import reindex, run_sync

    run_sync(PROJECT_ID, config=_config(max_tokens=512))
    before = _chunk_count()
    # Reindex with a smaller scheme — rebuilds purely from stored body.
    res = reindex(PROJECT_ID, config=_config(max_tokens=40))[0]
    assert res.reindexed == 1 and res.needs_refetch == 0
    assert _doc().chunk_scheme == "sliding-40-16"
    assert _chunk_count() != before


def test_store_body_off_marks_refetch() -> None:
    from universal_rag.ingestion import reindex, run_sync

    run_sync(PROJECT_ID, config=_config(store_body=False))
    assert _doc().body == ""
    res = reindex(PROJECT_ID, config=_config(store_body=False))[0]
    assert res.needs_refetch == 1 and res.reindexed == 0
    assert _doc().content_hash == ""  # cleared → next sync re-fetches
