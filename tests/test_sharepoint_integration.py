"""End-to-end SharePoint ingestion into real pgvector + Ollama.

Drives the REAL SharePointConnector.fetch() (drive walk, extension/size/recency
filter, file download, text extraction, mapping) against a MockTransport Graph —
no Azure tenant needed — then runs it through run_sync into pgvector. Skips when
Postgres or Ollama aren't reachable.
"""

from __future__ import annotations

import io
from collections.abc import Iterator

import httpx
import pytest

from universal_rag.config.schema import AppConfig, Defaults, ProjectConfig, SourceConfig
from universal_rag.connectors.sharepoint import MicrosoftGraphClient
from universal_rag.db.models import Chunk, Project
from universal_rag.db.session import get_engine, get_session

PROJECT_ID = "itest_sp"
SOURCE_ID = f"{PROJECT_ID}-sharepoint"


def _services_up() -> bool:
    try:
        get_engine().connect().close()
        from universal_rag.embeddings import OllamaEmbedder

        OllamaEmbedder().embed(["ping"])
        return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(not _services_up(), reason="Postgres/Ollama not available")


def _docx_bytes(text: str) -> bytes:
    from docx import Document

    doc = Document()
    doc.add_paragraph(text)
    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


def _graph_handler(request: httpx.Request) -> httpx.Response:
    path = request.url.path
    if ":" in path and "/sites/" in path:  # resolve_site
        return httpx.Response(200, json={"id": "site-1"})
    if path.endswith("/site-1/drives"):  # list_site_drives
        return httpx.Response(200, json={"value": [{"id": "drive-1"}]})
    if path.endswith("/drive-1/root/children"):  # iter_drive_items
        return httpx.Response(
            200,
            json={
                "value": [
                    {
                        "id": "i1",
                        "name": "Strategy.docx",
                        "size": 5000,
                        "lastModifiedDateTime": "2024-03-01T09:00:00Z",
                        "webUrl": "https://contoso.sharepoint.com/Strategy.docx",
                        "file": {"mimeType": "application/vnd.docx"},
                        "parentReference": {"path": "/drive/root:"},
                        "lastModifiedBy": {"user": {"displayName": "Ada"}},
                    },
                    {
                        "id": "i2",
                        "name": "logo.png",  # unsupported -> filtered out
                        "size": 200,
                        "lastModifiedDateTime": "2024-03-02T09:00:00Z",
                        "file": {"mimeType": "image/png"},
                    },
                ]
            },
        )
    if path.endswith("/items/i1/content"):  # download_item
        return httpx.Response(200, content=_docx_bytes("Apollo go-to-market strategy for Q3."))
    return httpx.Response(404, json={"error": path})


@pytest.fixture
def config() -> AppConfig:
    source = SourceConfig(
        id=SOURCE_ID, provider="sharepoint", sites=["contoso.sharepoint.com:/sites/Apollo"]
    )
    project = ProjectConfig(id=PROJECT_ID, name="ITestSP", description="", sources=[source])
    return AppConfig(defaults=Defaults(), projects=[project])


@pytest.fixture(autouse=True)
def _mock_graph(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    # Inject a MockTransport Graph client, bypassing token acquisition only.
    def fake_client(self: object) -> MicrosoftGraphClient:
        return MicrosoftGraphClient(
            "https://graph.microsoft.com/v1.0", "TOK", transport=httpx.MockTransport(_graph_handler)
        )

    monkeypatch.setattr(
        "universal_rag.connectors.sharepoint.SharePointConnector._client", fake_client
    )
    yield
    with get_session() as session:
        proj = session.get(Project, PROJECT_ID)
        if proj is not None:
            session.delete(proj)


def test_sharepoint_fetch_into_pgvector(config: AppConfig) -> None:
    from sqlalchemy import select

    from universal_rag.ingestion import run_sync

    results = run_sync(PROJECT_ID, config=config)
    assert len(results) == 1
    r = results[0]
    assert r.status == "ok", r.error
    assert r.documents == 1  # the .png was filtered; only the .docx ingested
    assert r.chunks >= 1

    with get_session() as session:
        rows = session.scalars(select(Chunk).where(Chunk.project_id == PROJECT_ID)).all()
        assert rows
        assert all(len(list(c.embedding)) == 768 for c in rows)
        assert all(c.chunk_metadata.get("source_kind") == "sharepoint" for c in rows)
        assert any("go-to-market" in c.content for c in rows)

    # Re-run: unchanged -> hash-skipped.
    again = run_sync(PROJECT_ID, config=config)
    assert again[0].documents == 0
    assert again[0].skipped == 1
