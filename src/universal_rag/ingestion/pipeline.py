"""Per-source incremental ingestion orchestration.

run_sync(project) -> for each source:
  seed Project/Source rows from config -> load cursor -> connector.fetch(cursor)
  -> hash-skip unchanged docs -> chunk -> embed (doc prefix) -> upsert
  Document+Chunks -> advance the cursor in sync_state.

Chunks denormalize project_id + source_id so retrieval can scope to one project
across providers in a single indexed query.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime

import structlog
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from universal_rag.config import load_app_config
from universal_rag.config.schema import (
    AppConfig,
    ChunkConfig,
    ProjectConfig,
    SourceConfig,
)
from universal_rag.connectors import SyncCursor, get_connector
from universal_rag.connectors.base import Connector, SourceDocument
from universal_rag.db.models import Chunk, Document, Project, Source, SyncState
from universal_rag.db.session import get_session
from universal_rag.embeddings import OllamaEmbedder
from universal_rag.ingestion.chunking import chunk_text

log = structlog.get_logger("universal_rag.ingestion")


@dataclass(slots=True)
class SourceSyncResult:
    source_id: str
    documents: int = 0
    chunks: int = 0
    skipped: int = 0
    deleted: int = 0  # docs pruned because they no longer exist at the source
    status: str = "ok"  # ok | error
    error: str = ""


def _hash(title: str, content: str) -> str:
    return hashlib.sha256(f"{title}\n{content}".encode()).hexdigest()


def ensure_project(session: Session, project: ProjectConfig) -> None:
    """Upsert the Project and its Source rows from config (idempotent)."""
    row = session.get(Project, project.id)
    if row is None:
        row = Project(id=project.id, name=project.name, description=project.description)
        session.add(row)
    else:
        row.name, row.description = project.name, project.description

    for src in project.sources:
        s = session.get(Source, src.id)
        if s is None:
            session.add(
                Source(id=src.id, project_id=project.id, provider=src.provider, config=src.options)
            )
        else:
            s.project_id, s.provider, s.config = project.id, src.provider, src.options
    session.flush()


class IngestionPipeline:
    def __init__(self, embedder: OllamaEmbedder | None = None, chunk: ChunkConfig | None = None):
        self.embedder = embedder or OllamaEmbedder()
        self.chunk_cfg = chunk or ChunkConfig()

    def _upsert_document(
        self, session: Session, project_id: str, source_id: str, doc: SourceDocument
    ) -> tuple[str, int]:
        """Returns (outcome, n_chunks); outcome is 'skipped' or 'written'."""
        content_hash = _hash(doc.title, doc.content)
        existing = session.scalar(
            select(Document).where(
                Document.source_id == source_id, Document.external_id == doc.external_id
            )
        )
        if existing is not None and existing.content_hash == content_hash:
            return ("skipped", 0)  # unchanged — skip re-embedding

        if existing is None:
            row = Document(
                project_id=project_id,
                source_id=source_id,
                external_id=doc.external_id,
            )
            session.add(row)
        else:
            row = existing
            session.execute(delete(Chunk).where(Chunk.document_id == row.id))

        row.title = doc.title
        row.url = doc.url
        row.content_hash = content_hash
        row.updated_at = doc.updated_at
        row.doc_metadata = doc.metadata
        session.flush()  # ensure row.id

        text_chunks = chunk_text(
            doc.content,
            max_tokens=self.chunk_cfg.max_tokens,
            overlap_tokens=self.chunk_cfg.overlap_tokens,
        )
        if not text_chunks:
            return ("written", 0)  # e.g. an empty page; the Document row still updates
        vectors = self.embedder.embed_documents([c.content for c in text_chunks])
        for tc, vec in zip(text_chunks, vectors, strict=True):
            session.add(
                Chunk(
                    document_id=row.id,
                    project_id=project_id,
                    source_id=source_id,
                    ordinal=tc.ordinal,
                    content=tc.content,
                    embedding=vec,
                    chunk_metadata={"title": doc.title, "url": doc.url, **doc.metadata},
                )
            )
        return ("written", len(text_chunks))

    def _delete_document(self, session: Session, source_id: str, external_id: str) -> int:
        """Hard-delete one document (and its chunks) by external_id. Returns 0/1."""
        doc_id = session.scalar(
            select(Document.id).where(
                Document.source_id == source_id, Document.external_id == external_id
            )
        )
        if doc_id is None:
            return 0
        session.execute(delete(Chunk).where(Chunk.document_id == doc_id))
        session.execute(delete(Document).where(Document.id == doc_id))
        return 1

    def _prune(
        self,
        session: Session,
        connector: Connector,
        source: SourceConfig,
        result: SourceSyncResult,
    ) -> None:
        """Hard-delete documents whose external_id no longer exists at the source.

        Safety: skip when the connector can't enumerate, the enumeration fails, or the
        live set is empty — never delete on incomplete/uncertain data.
        """
        try:
            live = connector.list_external_ids()
        except Exception as exc:
            log.warning("prune.skipped", source=source.id, reason=f"enumeration failed: {exc}")
            return
        if live is None:
            return  # provider doesn't support enumeration → never prune
        if not live:
            log.warning("prune.skipped", source=source.id, reason="empty live id set")
            return

        stale = list(
            session.scalars(
                select(Document.id).where(
                    Document.source_id == source.id, Document.external_id.not_in(live)
                )
            ).all()
        )
        if stale:
            session.execute(delete(Chunk).where(Chunk.document_id.in_(stale)))
            session.execute(delete(Document).where(Document.id.in_(stale)))
        result.deleted = len(stale)

    def run(
        self,
        session: Session,
        project: ProjectConfig,
        source: SourceConfig,
        *,
        prune: bool = True,
    ) -> SourceSyncResult:
        result = SourceSyncResult(source_id=source.id)
        state = session.get(SyncState, source.id)
        if state is None:
            state = SyncState(source_id=source.id)
            session.add(state)
        state.last_status = "running"
        state.last_error = ""
        session.flush()

        cursor = SyncCursor(value=state.cursor) if state.cursor else None
        max_updated: datetime | None = None
        connector = get_connector(source)
        try:
            for doc in connector.fetch(cursor):
                if doc.deleted:  # change-feed deletion marker (e.g. SharePoint /delta)
                    result.deleted += self._delete_document(session, source.id, doc.external_id)
                    continue
                outcome, written = self._upsert_document(session, project.id, source.id, doc)
                if outcome == "skipped":
                    result.skipped += 1
                else:
                    result.documents += 1
                    result.chunks += written
                if doc.updated_at and (max_updated is None or doc.updated_at > max_updated):
                    max_updated = doc.updated_at
        except Exception as exc:  # one bad source shouldn't sink the whole run
            result.status = "error"
            result.error = f"{type(exc).__name__}: {exc}"
            state.last_status = "error"
            state.last_error = result.error
            state.last_run_at = datetime.now(UTC)
            return result

        # Reconcile deletions only after a clean fetch (safety rule #1).
        if prune:
            self._prune(session, connector, source, result)

        # Cursor: a connector-owned opaque value (e.g. /delta link) wins; otherwise
        # fall back to the newest updated_at we saw (timestamp-cursor connectors).
        next_cursor = connector.next_cursor()
        if next_cursor is not None:
            state.cursor = next_cursor
        elif max_updated is not None:
            state.cursor = max_updated.isoformat()
        state.last_status = "ok"
        state.last_run_at = datetime.now(UTC)
        return result


def run_sync(
    project_id: str,
    source_id: str | None = None,
    *,
    config: AppConfig | None = None,
    prune: bool | None = None,
) -> list[SourceSyncResult]:
    """Sync one project (optionally a single source) from config into pgvector.

    `prune` controls deletion reconciliation: None (default) uses each source's
    `prune` option (default True); True/False forces it for all synced sources.
    """
    cfg = config or load_app_config()
    project = cfg.project(project_id)
    if project is None:
        raise ValueError(f"Unknown project '{project_id}'")

    sources = project.sources
    if source_id is not None:
        sources = [s for s in sources if s.id == source_id]
        if not sources:
            raise ValueError(f"Unknown source '{source_id}' in project '{project_id}'")

    pipeline = IngestionPipeline(chunk=cfg.defaults.chunk)
    results: list[SourceSyncResult] = []
    for source in sources:
        effective_prune = prune if prune is not None else bool(source.opt("prune", True))
        # Each source gets its own transaction so a failure is isolated.
        with get_session() as session:
            ensure_project(session, project)
            results.append(pipeline.run(session, project, source, prune=effective_prune))
    return results
