"""API surface tests. The healthz/projects/404 cases need no services; the
query round-trip is guarded and skips when Postgres/Ollama aren't available.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from universal_rag.api.app import app

client = TestClient(app)


def test_healthz() -> None:
    r = client.get("/healthz")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_projects_lists_from_example_config() -> None:
    # No config.yaml in the repo -> falls back to config.example.yaml.
    ids = {p["id"] for p in client.get("/projects").json()}
    assert "apollo" in ids


def test_sync_unknown_project_is_404() -> None:
    assert client.post("/projects/does-not-exist/sync").status_code == 404


# --- query round-trip (needs services) ------------------------------------- #
PROJECT_ID = "itest_api"
SOURCE_ID = f"{PROJECT_ID}-confluence"


def _services_up() -> bool:
    try:
        from universal_rag.db.session import get_engine
        from universal_rag.embeddings import OllamaEmbedder

        get_engine().connect().close()
        OllamaEmbedder().embed(["ping"])
        return True
    except Exception:
        return False


@pytest.mark.skipif(not _services_up(), reason="Postgres/Ollama not available")
def test_query_roundtrip() -> None:
    import universal_rag.ingestion.pipeline as pipeline_mod
    from universal_rag.config.schema import AppConfig, Defaults, ProjectConfig, SourceConfig
    from universal_rag.connectors.base import Connector, SourceDocument, SyncCursor
    from universal_rag.db.models import Project
    from universal_rag.db.session import get_session
    from universal_rag.ingestion import run_sync

    class _Fake(Connector):
        provider = "confluence"

        def fetch(self, cursor: SyncCursor | None = None) -> Iterator[SourceDocument]:
            yield SourceDocument(
                source_id=self.source.id,
                provider="confluence",
                external_id="1",
                title="Auth Service",
                content="Authentication uses OAuth2 with short-lived JWT tokens. " * 12,
                url="https://conf/1",
                updated_at=datetime(2024, 3, 1, tzinfo=UTC),
                metadata={"space": "APOLLO"},
            )

    cfg = AppConfig(
        defaults=Defaults(),
        projects=[
            ProjectConfig(
                id=PROJECT_ID,
                name="API ITest",
                sources=[SourceConfig(id=SOURCE_ID, provider="confluence", spaces=["APOLLO"])],
            )
        ],
    )
    original = pipeline_mod.get_connector
    pipeline_mod.get_connector = lambda s: _Fake(s)  # type: ignore[assignment]
    try:
        run_sync(PROJECT_ID, config=cfg)
        r = client.post(
            f"/projects/{PROJECT_ID}/query",
            json={"query": "how do users authenticate", "top_k": 3},
        )
        assert r.status_code == 200
        body = r.json()
        assert body["results"]
        assert body["results"][0]["title"] == "Auth Service"
        assert body["results"][0]["url"] == "https://conf/1"
    finally:
        pipeline_mod.get_connector = original  # type: ignore[assignment]
        with get_session() as session:
            proj = session.get(Project, PROJECT_ID)
            if proj is not None:
                session.delete(proj)
