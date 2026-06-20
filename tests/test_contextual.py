from __future__ import annotations

from collections.abc import Iterator

import pytest

from universal_rag.ingestion.contextualize import Contextualizer


def _ctx(reply: str | None) -> Contextualizer:
    c = Contextualizer.__new__(Contextualizer)  # bypass __init__ (no real ollama client)
    c.model = "x"

    class _Client:
        def chat(self, **kw: object) -> dict:
            if reply is None:
                raise RuntimeError("model down")
            return {"message": {"content": reply}}

    c._client = _Client()  # type: ignore[attr-defined]
    return c


def test_contextualize_prepends_context() -> None:
    out = _ctx("From the Q3 analytics roadmap.").contextualize("…body…", "Phase one ships.")
    assert out == "From the Q3 analytics roadmap.\n\nPhase one ships."


def test_contextualize_falls_back_to_bare_chunk_on_failure() -> None:
    assert _ctx(None).contextualize("body", "Phase one ships.") == "Phase one ships."


# --------------------------------------------------------------------------- #
# Live: contextualized text is embedded + stored + FTS-indexed
# --------------------------------------------------------------------------- #
PROJECT_ID = "itest_ctx"
SOURCE_ID = f"{PROJECT_ID}-corpus"


def _services_up() -> bool:
    from universal_rag.db.session import get_engine

    try:
        get_engine().connect().close()
        from universal_rag.embeddings import OllamaEmbedder

        OllamaEmbedder().embed(["ping"])
        return True
    except Exception:
        return False


@pytest.fixture(autouse=True)
def _cleanup() -> Iterator[None]:
    yield
    from universal_rag.db.models import Project
    from universal_rag.db.session import get_session

    with get_session() as s:
        p = s.get(Project, PROJECT_ID)
        if p is not None:
            s.delete(p)


@pytest.mark.skipif(not _services_up(), reason="Postgres/Ollama not available")
def test_contextual_ingest_stores_contextualized_content(monkeypatch: pytest.MonkeyPatch) -> None:
    from sqlalchemy import select

    from universal_rag.config import get_settings
    from universal_rag.config.schema import ChunkConfig
    from universal_rag.connectors.base import SourceDocument
    from universal_rag.db.models import Chunk, Document, Project, Source
    from universal_rag.db.session import get_session
    from universal_rag.ingestion.pipeline import IngestionPipeline

    monkeypatch.setenv("CONTEXTUALIZE_MODEL", "dummy")
    get_settings.cache_clear()
    try:
        pipeline = IngestionPipeline(chunk=ChunkConfig(contextualize=True))

        class _Stub:
            def contextualize(self, body: str, chunk: str) -> str:
                return f"CONTEXT_PREFIX :: {chunk}"

        pipeline.contextualizer = _Stub()  # type: ignore[assignment]
        assert pipeline.scheme() == "contextual-512-64"

        doc = SourceDocument(
            source_id=SOURCE_ID,
            provider="beir",
            external_id="1",
            title="Doc",
            content="Phase one ships the analytics dashboard in Q3.",
            metadata={},
        )
        with get_session() as s:
            s.add(Project(id=PROJECT_ID, name="ctx"))
            s.add(Source(id=SOURCE_ID, project_id=PROJECT_ID, provider="beir", config={}))
            pipeline.bulk_index(s, PROJECT_ID, SOURCE_ID, [doc])

        with get_session() as s:
            chunk = s.scalars(select(Chunk).where(Chunk.source_id == SOURCE_ID)).one()
            assert chunk.content.startswith("CONTEXT_PREFIX ::")  # contextualized text stored
            assert chunk.chunk_scheme == "contextual-512-64"
            # body keeps the original (raw) text for re-index
            d = s.scalars(select(Document).where(Document.source_id == SOURCE_ID)).one()
            assert d.body == "Phase one ships the analytics dashboard in Q3."
    finally:
        get_settings.cache_clear()
