"""End-to-end retrieval over real pgvector + Ollama.

Ingests a few distinct fake documents through the real pipeline, then exercises
the hybrid retriever: semantic relevance, exact-keyword recall, and project +
source scoping. Skips automatically when Postgres or Ollama aren't reachable.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime

import pytest

from universal_rag.config.schema import AppConfig, Defaults, ProjectConfig, SourceConfig
from universal_rag.connectors.base import Connector, SourceDocument, SyncCursor
from universal_rag.db.models import Project
from universal_rag.db.session import get_engine, get_session

PROJECT_ID = "itest_retr"
SOURCE_ID = f"{PROJECT_ID}-confluence"


def _services_up() -> bool:
    try:
        get_engine().connect().close()
        from universal_rag.embeddings import OllamaEmbedder

        OllamaEmbedder().embed(["ping"])
        return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(not _services_up(), reason="Postgres/Ollama not available")

_DOCS = {
    "200": (
        "Analytics Dashboard",
        "The analytics dashboard visualizes customer revenue trends in real time. " * 12,
    ),
    "201": (
        "Auth Service",
        "User authentication uses OAuth2 and short-lived JWT access tokens. " * 12,
    ),
    "202": (
        "Deployment Runbook",
        "Deploy with the Kubernetes ZephyrPipeline operator and rollback on failure. " * 12,
    ),
}


class _FakeConfluence(Connector):
    provider = "confluence"

    def fetch(self, cursor: SyncCursor | None = None) -> Iterator[SourceDocument]:
        when = datetime(2024, 3, 1, 9, 0, tzinfo=UTC)
        for ext_id, (title, body) in _DOCS.items():
            yield SourceDocument(
                source_id=self.source.id,
                provider="confluence",
                external_id=ext_id,
                title=title,
                content=body,
                url=f"https://confluence.acme.com/p/{ext_id}",
                updated_at=when,
                metadata={"space": "APOLLO"},
            )


@pytest.fixture(autouse=True)
def _ingested() -> Iterator[None]:
    import universal_rag.ingestion.pipeline as pipeline_mod
    from universal_rag.ingestion import run_sync

    source = SourceConfig(id=SOURCE_ID, provider="confluence", spaces=["APOLLO"])
    project = ProjectConfig(id=PROJECT_ID, name="ITestRetr", description="", sources=[source])
    config = AppConfig(defaults=Defaults(), projects=[project])

    original = pipeline_mod.get_connector
    pipeline_mod.get_connector = lambda s: _FakeConfluence(s)  # type: ignore[assignment]
    try:
        run_sync(PROJECT_ID, config=config)
        yield
    finally:
        pipeline_mod.get_connector = original  # type: ignore[assignment]
        with get_session() as session:
            proj = session.get(Project, PROJECT_ID)
            if proj is not None:
                session.delete(proj)


def test_semantic_match_ranks_relevant_doc_first() -> None:
    from universal_rag.retrieval import HybridRetriever

    hits = HybridRetriever(top_k=5).search("how do users log in and get tokens", PROJECT_ID)
    assert hits
    assert hits[0].title == "Auth Service"


def test_exact_keyword_is_recalled() -> None:
    from universal_rag.retrieval import HybridRetriever

    # "ZephyrPipeline" is a rare exact token the keyword arm should surface.
    hits = HybridRetriever(top_k=5).search("ZephyrPipeline operator", PROJECT_ID)
    assert any(h.title == "Deployment Runbook" for h in hits)


def test_scoping_filters_to_project_and_source() -> None:
    from universal_rag.retrieval import HybridRetriever

    hits = HybridRetriever(top_k=5).search("dashboard", PROJECT_ID, source_ids=[SOURCE_ID])
    assert hits
    assert all(h.project_id == PROJECT_ID and h.source_id == SOURCE_ID for h in hits)
    # querying a different (nonexistent) project returns nothing
    assert HybridRetriever().search("dashboard", "no_such_project") == []
