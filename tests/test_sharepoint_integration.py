"""End-to-end SharePoint /delta ingestion into real pgvector + Ollama.

Drives the REAL SharePointConnector.fetch() (site/drive resolution, /delta paging,
file download, text extraction, deletion tombstones, per-drive deltaLink cursor)
against a MockTransport Graph — no Azure tenant needed. Skips when Postgres or
Ollama aren't reachable.
"""

from __future__ import annotations

import io
from collections.abc import Iterator

import httpx
import pytest

from universal_rag.config.schema import AppConfig, Defaults, ProjectConfig, SourceConfig
from universal_rag.connectors.sharepoint import MicrosoftGraphClient
from universal_rag.db.models import Chunk, Document, Project
from universal_rag.db.session import get_engine, get_session

PROJECT_ID = "itest_sp"
SOURCE_ID = f"{PROJECT_ID}-sharepoint"
_DELTA = "https://graph.microsoft.com/v1.0/drives/drive-1/root/delta"


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
    token = request.url.params.get("token")
    if ":" in path and "/sites/" in path:  # resolve_site
        return httpx.Response(200, json={"id": "site-1"})
    if path.endswith("/site-1/drives"):  # list_site_drives
        return httpx.Response(200, json={"value": [{"id": "drive-1"}]})
    if path.endswith("/items/i1/content"):  # download_item
        return httpx.Response(200, content=_docx_bytes("Apollo go-to-market strategy for Q3."))
    if path.endswith("/drive-1/root/delta"):
        if token is None:  # initial enumeration
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
                            "name": "logo.png",
                            "size": 200,
                            "file": {"mimeType": "image/png"},
                        },
                    ],
                    "@odata.deltaLink": f"{_DELTA}?token=T2",
                },
            )
        if token == "T2":  # i1 deleted at the source
            return httpx.Response(
                200,
                json={
                    "value": [{"id": "i1", "deleted": {"state": "deleted"}}],
                    "@odata.deltaLink": f"{_DELTA}?token=T3",
                },
            )
        return httpx.Response(200, json={"value": [], "@odata.deltaLink": f"{_DELTA}?token=T4"})
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


def _doc_ids() -> set[str]:
    from sqlalchemy import select

    with get_session() as session:
        return set(
            session.scalars(select(Document.external_id).where(Document.source_id == SOURCE_ID))
        )


def test_sharepoint_delta_ingests_then_deletes(config: AppConfig) -> None:
    from sqlalchemy import select

    from universal_rag.ingestion import run_sync

    # Run 1: initial /delta → ingest the .docx (the .png is filtered out).
    r = run_sync(PROJECT_ID, config=config)[0]
    assert r.status == "ok", r.error
    assert r.documents == 1
    assert r.chunks >= 1
    assert _doc_ids() == {"drive-1:i1"}

    with get_session() as session:
        rows = session.scalars(select(Chunk).where(Chunk.project_id == PROJECT_ID)).all()
        assert all(len(list(c.embedding)) == 768 for c in rows)
        assert all(c.chunk_metadata.get("source_kind") == "sharepoint" for c in rows)
        assert any("go-to-market" in c.content for c in rows)

    # Run 2: /delta (token T2) reports i1 deleted → pruned via the tombstone.
    r2 = run_sync(PROJECT_ID, config=config)[0]
    assert r2.deleted == 1
    assert r2.documents == 0
    assert _doc_ids() == set()
