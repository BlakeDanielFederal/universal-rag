"""Deletion handling: per-connector id enumeration + pipeline prune/safety."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime

import httpx
import pytest
from sqlalchemy import func, select

from universal_rag.config.schema import AppConfig, Defaults, ProjectConfig, SourceConfig
from universal_rag.connectors import confluence as cf
from universal_rag.connectors import github as gh
from universal_rag.connectors import jira as jr
from universal_rag.connectors import sharepoint as sp
from universal_rag.connectors.base import Connector, SourceDocument, SyncCursor
from universal_rag.db.models import Chunk, Document, Project
from universal_rag.db.session import get_engine, get_session


def _mock(handler) -> httpx.MockTransport:
    return httpx.MockTransport(handler)


# --------------------------------------------------------------------------- #
# Per-connector list_external_ids — ids must match fetch()'s external_id format
# --------------------------------------------------------------------------- #
def test_confluence_list_external_ids(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"results": [{"id": "1"}, {"id": "2"}], "limit": 50})

    client = cf.ConfluenceDataCenterClient("https://c", "T", transport=_mock(handler))
    conn = cf.ConfluenceConnector(SourceConfig(id="s", provider="confluence", spaces=["APOLLO"]))
    monkeypatch.setattr(conn, "_client", lambda: client)
    assert conn.list_external_ids() == {"1", "2"}


def test_jira_list_external_ids(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["fields"] == "key"  # ids only
        return httpx.Response(
            200, json={"issues": [{"key": "APOL-1"}, {"key": "APOL-2"}], "total": 2}
        )

    client = jr.JiraDataCenterClient("https://j", "T", transport=_mock(handler))
    conn = jr.JiraConnector(SourceConfig(id="s", provider="jira", projects=["APOL"]))
    monkeypatch.setattr(conn, "_client", lambda: client)
    assert conn.list_external_ids() == {"APOL-1", "APOL-2"}


def test_github_list_external_ids(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/issues"):
            return httpx.Response(
                200, json=[{"number": 1}, {"number": 2, "pull_request": {"url": "x"}}]
            )
        if path.endswith("/releases"):
            return httpx.Response(200, json=[{"id": 9}])
        if path.endswith("/readme"):
            return httpx.Response(200, json={"path": "README.md"})
        return httpx.Response(404, json={})

    client = gh.GitHubClient("T", transport=_mock(handler))
    conn = gh.GitHubConnector(SourceConfig(id="s", provider="github", repos=["o/r"]))
    monkeypatch.setattr(conn, "_client", lambda: client)
    assert conn.list_external_ids() == {
        "o/r#issue-1",
        "o/r#pr-2",
        "o/r#release-9",
        "o/r#readme",
    }


def test_sharepoint_opts_out_of_reconcile_prune() -> None:
    # SharePoint handles deletions via the /delta feed, so it opts out of the
    # full-reconcile prune by returning None from list_external_ids.
    conn = sp.SharePointConnector(
        SourceConfig(id="s", provider="sharepoint", sites=["contoso.sharepoint.com:/sites/Apollo"])
    )
    assert conn.list_external_ids() is None


# --------------------------------------------------------------------------- #
# Pipeline prune + safety (live: real pgvector + Ollama)
# --------------------------------------------------------------------------- #
PROJECT_ID = "itest_del"
SOURCE_ID = f"{PROJECT_ID}-confluence"
_RAISE = object()


def _services_up() -> bool:
    try:
        get_engine().connect().close()
        from universal_rag.embeddings import OllamaEmbedder

        OllamaEmbedder().embed(["ping"])
        return True
    except Exception:
        return False


live_pytestmark = pytest.mark.skipif(not _services_up(), reason="Postgres/Ollama not available")


class _FakeConn(Connector):
    provider = "confluence"
    yield_ids: list[str] = []
    live: object = None  # set[str] | None | _RAISE

    def fetch(self, cursor: SyncCursor | None = None) -> Iterator[SourceDocument]:
        when = datetime(2024, 3, 1, tzinfo=UTC)
        for ext in self.yield_ids:
            yield SourceDocument(
                source_id=self.source.id,
                provider="confluence",
                external_id=ext,
                title=f"Doc {ext}",
                content=f"content for document {ext}. " * 20,
                updated_at=when,
                metadata={"space": "APOLLO"},
            )

    def list_external_ids(self) -> set[str] | None:
        if self.live is _RAISE:
            raise RuntimeError("enumeration failed")
        return self.live  # type: ignore[return-value]


@pytest.fixture
def config() -> AppConfig:
    source = SourceConfig(id=SOURCE_ID, provider="confluence", spaces=["APOLLO"])
    project = ProjectConfig(id=PROJECT_ID, name="ITestDel", description="", sources=[source])
    return AppConfig(defaults=Defaults(), projects=[project])


@pytest.fixture(autouse=True)
def _cleanup() -> Iterator[None]:
    yield
    with get_session() as session:
        proj = session.get(Project, PROJECT_ID)
        if proj is not None:
            session.delete(proj)


def _run(monkeypatch, config, *, yield_ids, live, prune=None):
    from universal_rag.ingestion import run_sync

    _FakeConn.yield_ids = yield_ids
    _FakeConn.live = live
    monkeypatch.setattr("universal_rag.ingestion.pipeline.get_connector", lambda s: _FakeConn(s))
    return run_sync(PROJECT_ID, config=config, prune=prune)


def _db_ids() -> set[str]:
    with get_session() as s:
        return set(s.scalars(select(Document.external_id).where(Document.source_id == SOURCE_ID)))


def _chunk_count() -> int:
    with get_session() as s:
        return (
            s.scalar(select(func.count()).select_from(Chunk).where(Chunk.source_id == SOURCE_ID))
            or 0
        )


@live_pytestmark
def test_prune_removes_items_deleted_at_source(monkeypatch: pytest.MonkeyPatch, config) -> None:
    # Ingest 3 docs (all live).
    r = _run(monkeypatch, config, yield_ids=["1", "2", "3"], live={"1", "2", "3"})[0]
    assert r.documents == 3 and r.deleted == 0
    assert _db_ids() == {"1", "2", "3"}

    # Next run: "3" is gone at the source -> pruned (its chunks too).
    r2 = _run(monkeypatch, config, yield_ids=[], live={"1", "2"})[0]
    assert r2.deleted == 1
    assert _db_ids() == {"1", "2"}
    # only chunks for the two survivors remain
    assert _chunk_count() == 2


@live_pytestmark
def test_empty_live_set_never_prunes(monkeypatch: pytest.MonkeyPatch, config) -> None:
    _run(monkeypatch, config, yield_ids=["1", "2", "3"], live={"1", "2", "3"})
    r = _run(monkeypatch, config, yield_ids=[], live=set())[0]  # empty -> safety guard
    assert r.status == "ok" and r.deleted == 0
    assert _db_ids() == {"1", "2", "3"}


@live_pytestmark
def test_enumeration_error_never_prunes(monkeypatch: pytest.MonkeyPatch, config) -> None:
    _run(monkeypatch, config, yield_ids=["1", "2", "3"], live={"1", "2", "3"})
    r = _run(monkeypatch, config, yield_ids=[], live=_RAISE)[0]  # raises -> skip prune
    assert r.status == "ok" and r.deleted == 0
    assert _db_ids() == {"1", "2", "3"}


@live_pytestmark
def test_prune_disabled_keeps_stale(monkeypatch: pytest.MonkeyPatch, config) -> None:
    _run(monkeypatch, config, yield_ids=["1", "2", "3"], live={"1", "2", "3"})
    r = _run(monkeypatch, config, yield_ids=[], live={"1"}, prune=False)[0]
    assert r.deleted == 0
    assert _db_ids() == {"1", "2", "3"}
