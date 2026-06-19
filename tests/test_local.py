from __future__ import annotations

import io
import os
from datetime import UTC, datetime, timedelta

import pytest

from universal_rag.config.schema import AppConfig, Defaults, ProjectConfig, SourceConfig
from universal_rag.connectors.base import SyncCursor
from universal_rag.connectors.local import LocalDirectoryConnector


def _conn(tmp_path, **opts) -> LocalDirectoryConnector:
    return LocalDirectoryConnector(
        SourceConfig(id="s", provider="local", paths=[str(tmp_path)], **opts)
    )


def _docx_bytes(text: str) -> bytes:
    from docx import Document

    doc = Document()
    doc.add_paragraph(text)
    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


def test_fetch_extracts_supported_files_and_skips_others(tmp_path) -> None:
    (tmp_path / "notes.md").write_text("# Roadmap\nApollo phase one")
    (tmp_path / "plan.docx").write_bytes(_docx_bytes("Apollo strategy document"))
    (tmp_path / "logo.png").write_bytes(b"\x89PNG\r\n")  # unsupported -> skipped
    sub = tmp_path / "sub"
    sub.mkdir()
    (sub / "deep.txt").write_text("nested content here")

    docs = list(_conn(tmp_path).fetch())
    by_name = {os.path.basename(d.external_id): d for d in docs}
    assert set(by_name) == {"notes.md", "plan.docx", "deep.txt"}  # png skipped
    assert "Apollo phase one" in by_name["notes.md"].content
    assert "Apollo strategy document" in by_name["plan.docx"].content
    assert by_name["notes.md"].external_id == str(tmp_path / "notes.md")  # abspath id
    assert by_name["notes.md"].provider == "local"


def test_exclude_globs_and_extension_filter(tmp_path) -> None:
    (tmp_path / "keep.md").write_text("keep me")
    (tmp_path / "skip.md").write_text("skip me")
    (tmp_path / "data.json").write_text('{"a": 1}')

    conn = _conn(tmp_path, exclude_globs=["skip.*"], include_extensions=[".md"])
    ids = conn.list_external_ids()
    assert ids == {str(tmp_path / "keep.md")}  # skip.md excluded, data.json wrong ext


def test_incremental_mtime_filter(tmp_path) -> None:
    old = tmp_path / "old.txt"
    new = tmp_path / "new.txt"
    old.write_text("old content")
    new.write_text("new content")
    now = datetime.now(UTC)
    os.utime(old, ((now - timedelta(hours=2)).timestamp(),) * 2)
    os.utime(new, ((now + timedelta(hours=2)).timestamp(),) * 2)

    cursor = SyncCursor(value=now.isoformat())
    fetched = {os.path.basename(d.external_id) for d in _conn(tmp_path).fetch(cursor)}
    assert fetched == {"new.txt"}  # only the file modified after the cursor
    # list_external_ids ignores the cursor (full scope for reconcile)
    assert {os.path.basename(p) for p in _conn(tmp_path).list_external_ids()} == {
        "old.txt",
        "new.txt",
    }


# --------------------------------------------------------------------------- #
# Live: ingest a dir, delete a file, prune on re-sync
# --------------------------------------------------------------------------- #
PROJECT_ID = "itest_local"
SOURCE_ID = f"{PROJECT_ID}-local"


def _services_up() -> bool:
    from universal_rag.db.session import get_engine

    try:
        get_engine().connect().close()
        from universal_rag.embeddings import OllamaEmbedder

        OllamaEmbedder().embed(["ping"])
        return True
    except Exception:
        return False


@pytest.mark.skipif(not _services_up(), reason="Postgres/Ollama not available")
def test_local_ingest_then_prune_deleted_file(tmp_path) -> None:
    from sqlalchemy import select

    from universal_rag.db.models import Document, Project
    from universal_rag.db.session import get_session
    from universal_rag.ingestion import run_sync

    (tmp_path / "a.md").write_text("Apollo analytics dashboard plan. " * 10)
    (tmp_path / "b.md").write_text("Apollo authentication service notes. " * 10)
    source = SourceConfig(id=SOURCE_ID, provider="local", paths=[str(tmp_path)])
    config = AppConfig(
        defaults=Defaults(), projects=[ProjectConfig(id=PROJECT_ID, name="L", sources=[source])]
    )

    def _ids() -> set[str]:
        with get_session() as s:
            rows = s.scalars(select(Document.external_id).where(Document.source_id == SOURCE_ID))
            return {os.path.basename(p) for p in rows}

    try:
        r = run_sync(PROJECT_ID, config=config)[0]
        assert r.documents == 2 and r.status == "ok"
        assert _ids() == {"a.md", "b.md"}

        (tmp_path / "b.md").unlink()  # delete a file at the source
        r2 = run_sync(PROJECT_ID, config=config)[0]
        assert r2.deleted == 1
        assert _ids() == {"a.md"}
    finally:
        with get_session() as s:
            proj = s.get(Project, PROJECT_ID)
            if proj is not None:
                s.delete(proj)
