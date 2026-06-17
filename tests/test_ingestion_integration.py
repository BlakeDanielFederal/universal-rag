"""End-to-end ingestion into the real pgvector store + Ollama embeddings.

Uses a fake connector (no Confluence instance needed) so it exercises the full
pipeline: seed -> chunk -> embed -> upsert -> cursor, and the incremental skip.
Skips automatically when Postgres or Ollama aren't reachable.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime

import pytest
from sqlalchemy import select

from universal_rag.config.schema import AppConfig, Defaults, ProjectConfig, SourceConfig
from universal_rag.connectors.base import Connector, SourceDocument, SyncCursor
from universal_rag.db.models import Chunk, Project
from universal_rag.db.session import get_engine, get_session

PROJECT_ID = "itest_conf"


def _services_up() -> bool:
    try:
        get_engine().connect().close()
        from universal_rag.embeddings import OllamaEmbedder

        OllamaEmbedder().embed(["ping"])
        return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(not _services_up(), reason="Postgres/Ollama not available")


class _FakeConfluence(Connector):
    provider = "confluence"

    def fetch(self, cursor: SyncCursor | None = None) -> Iterator[SourceDocument]:
        when = datetime(2024, 3, 1, 9, 0, tzinfo=UTC)
        yield SourceDocument(
            source_id=self.source.id,
            provider="confluence",
            external_id="100",
            title="Apollo Roadmap",
            content="Phase one delivers the analytics dashboard in Q3. " * 20,
            url="https://confluence.acme.com/display/APOLLO/Roadmap",
            updated_at=when,
            metadata={"space": "APOLLO", "version": 3},
        )
        yield SourceDocument(
            source_id=self.source.id,
            provider="confluence",
            external_id="101",
            title="Apollo Architecture",
            content="The service uses Postgres and a vector index for retrieval. " * 20,
            url="https://confluence.acme.com/display/APOLLO/Arch",
            updated_at=when,
            metadata={"space": "APOLLO", "version": 1},
        )


@pytest.fixture
def config() -> AppConfig:
    source = SourceConfig(id=f"{PROJECT_ID}-confluence", provider="confluence", spaces=["APOLLO"])
    project = ProjectConfig(id=PROJECT_ID, name="ITest", description="", sources=[source])
    return AppConfig(defaults=Defaults(), projects=[project])


@pytest.fixture(autouse=True)
def _cleanup() -> Iterator[None]:
    yield
    with get_session() as session:
        proj = session.get(Project, PROJECT_ID)
        if proj is not None:
            session.delete(proj)  # cascades to sources/documents/chunks/sync_state


def test_full_ingestion_into_pgvector(monkeypatch: pytest.MonkeyPatch, config: AppConfig) -> None:
    from universal_rag.ingestion import run_sync

    monkeypatch.setattr(
        "universal_rag.ingestion.pipeline.get_connector",
        lambda source: _FakeConfluence(source),
    )

    # First run ingests both documents.
    results = run_sync(PROJECT_ID, config=config)
    assert len(results) == 1
    r = results[0]
    assert r.status == "ok"
    assert r.documents == 2
    assert r.chunks > 0
    assert r.skipped == 0

    with get_session() as session:
        rows = session.scalars(select(Chunk).where(Chunk.project_id == PROJECT_ID)).all()
        assert len(rows) == r.chunks
        # embeddings are the right dimensionality and project-scoped
        assert all(len(list(c.embedding)) == 768 for c in rows)
        assert {c.source_id for c in rows} == {f"{PROJECT_ID}-confluence"}
        assert any("dashboard" in c.content for c in rows)

    # Second run: nothing changed -> both docs hash-skipped, no new chunks.
    results2 = run_sync(PROJECT_ID, config=config)
    assert results2[0].documents == 0
    assert results2[0].skipped == 2
