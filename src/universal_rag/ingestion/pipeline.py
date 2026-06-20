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
from universal_rag.ingestion.chunking import TextChunk, chunk_text

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
        self.contextualizer = None
        if self.chunk_cfg.contextualize:
            from universal_rag.config import get_settings
            from universal_rag.ingestion.contextualize import Contextualizer

            model = get_settings().contextualize_model
            if not model:
                raise RuntimeError(
                    "chunk.contextualize is on but CONTEXTUALIZE_MODEL is not set"
                )
            self.contextualizer = Contextualizer(model)

    def scheme(self) -> str:
        """Chunking signature; changes here trigger a controlled re-index. Word mode
        keeps the legacy 'sliding-' prefix so existing chunks aren't invalidated."""
        if self.chunk_cfg.contextualize:
            prefix = "contextual"
        elif self.chunk_cfg.split == "word":
            prefix = "sliding"
        else:
            prefix = self.chunk_cfg.split
        return f"{prefix}-{self.chunk_cfg.max_tokens}-{self.chunk_cfg.overlap_tokens}"

    def _chunk(self, content: str) -> list[TextChunk]:
        return chunk_text(
            content,
            max_tokens=self.chunk_cfg.max_tokens,
            overlap_tokens=self.chunk_cfg.overlap_tokens,
            split=self.chunk_cfg.split,
        )

    def _index_text(self, body: str, chunk: TextChunk) -> str:
        """The text that gets embedded, FTS-indexed, and stored for a chunk — the
        contextualized form when Contextual Retrieval is on, else the raw chunk."""
        if self.contextualizer is None:
            return chunk.content
        return self.contextualizer.contextualize(body, chunk.content)

    def _write_chunks(self, session: Session, row: Document, content: str) -> int:
        """Chunk + embed `content` into Chunk rows for an existing Document. Stamps
        the index signature. Reused by ingestion and re-index."""
        text_chunks = self._chunk(content)
        if not text_chunks:
            return 0
        meta = {"title": row.title, "url": row.url, **(row.doc_metadata or {})}
        index_texts = [self._index_text(content, tc) for tc in text_chunks]
        vectors = self.embedder.embed_documents(index_texts)
        for tc, text, vec in zip(text_chunks, index_texts, vectors, strict=True):
            session.add(
                Chunk(
                    document_id=row.id,
                    project_id=row.project_id,
                    source_id=row.source_id,
                    ordinal=tc.ordinal,
                    content=text,
                    embedding=vec,
                    embedding_model=self.embedder.model,
                    chunk_scheme=self.scheme(),
                    chunk_metadata=meta,
                )
            )
        return len(text_chunks)

    def _upsert_document(
        self,
        session: Session,
        project_id: str,
        source_id: str,
        doc: SourceDocument,
        *,
        store_body: bool = True,
    ) -> tuple[str, int]:
        """Returns (outcome, n_chunks); outcome is 'skipped' or 'written'."""
        content_hash = _hash(doc.title, doc.content)
        model, scheme = self.embedder.model, self.scheme()
        existing = session.scalar(
            select(Document).where(
                Document.source_id == source_id, Document.external_id == doc.external_id
            )
        )
        # Skip only when content AND the index signature are unchanged — a model or
        # chunk-scheme change makes existing chunks stale and forces a rebuild.
        if (
            existing is not None
            and existing.content_hash == content_hash
            and existing.embedding_model == model
            and existing.chunk_scheme == scheme
        ):
            return ("skipped", 0)

        if existing is None:
            row = Document(project_id=project_id, source_id=source_id, external_id=doc.external_id)
            session.add(row)
        else:
            row = existing
            session.execute(delete(Chunk).where(Chunk.document_id == row.id))

        row.title = doc.title
        row.url = doc.url
        row.content_hash = content_hash
        row.updated_at = doc.updated_at
        row.doc_metadata = doc.metadata
        row.body = doc.content if store_body else ""
        row.embedding_model = model
        row.chunk_scheme = scheme
        session.flush()  # ensure row.id

        n = self._write_chunks(session, row, doc.content)
        return ("written", n)

    def reindex_document(self, session: Session, row: Document) -> int:
        """Rebuild a document's chunks from its stored body with the current
        signature (no provider re-fetch). Returns n_chunks; -1 if no body."""
        if not row.body:
            return -1
        session.execute(delete(Chunk).where(Chunk.document_id == row.id))
        row.embedding_model = self.embedder.model
        row.chunk_scheme = self.scheme()
        return self._write_chunks(session, row, row.body)

    def bulk_index(
        self,
        session: Session,
        project_id: str,
        source_id: str,
        docs: list[SourceDocument],
        *,
        store_body: bool = True,
        embed_batch: int = 128,
    ) -> tuple[int, int]:
        """Insert many documents at once, embedding chunks in large cross-document
        batches (one Ollama call per `embed_batch` chunks). For bulk corpus imports
        where per-document embedding would be far too slow. Returns (docs, chunks).

        This is an insert-only fast path (no hash-skip / upsert) — intended for a
        fresh corpus. Caller owns the transaction (commit per page).
        """
        model, scheme = self.embedder.model, self.scheme()
        pending: list[tuple[Document, int, str]] = []  # (row, ordinal, index_text)
        texts: list[str] = []
        n_docs = n_chunks = 0

        def flush() -> None:
            nonlocal n_chunks
            if not texts:
                return
            vectors = self.embedder.embed_documents(texts)
            for (row, ordinal, text), vec in zip(pending, vectors, strict=True):
                meta = {"title": row.title, "url": row.url, **(row.doc_metadata or {})}
                session.add(
                    Chunk(
                        document_id=row.id,
                        project_id=project_id,
                        source_id=source_id,
                        ordinal=ordinal,
                        content=text,
                        embedding=vec,
                        embedding_model=model,
                        chunk_scheme=scheme,
                        chunk_metadata=meta,
                    )
                )
            n_chunks += len(texts)
            texts.clear()
            pending.clear()

        for doc in docs:
            row = Document(
                project_id=project_id,
                source_id=source_id,
                external_id=doc.external_id,
                title=doc.title,
                url=doc.url,
                content_hash=_hash(doc.title, doc.content),
                updated_at=doc.updated_at,
                doc_metadata=doc.metadata,
                body=doc.content if store_body else "",
                embedding_model=model,
                chunk_scheme=scheme,
            )
            session.add(row)
            session.flush()  # assign row.id before its chunks reference it
            n_docs += 1
            for tc in self._chunk(doc.content):
                pending.append((row, tc.ordinal, self._index_text(doc.content, tc)))
                texts.append(pending[-1][2])
                if len(texts) >= embed_batch:
                    flush()
        flush()
        return n_docs, n_chunks

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
        store_body = bool(source.opt("store_body", True))
        connector = get_connector(source)
        try:
            for doc in connector.fetch(cursor):
                if doc.deleted:  # change-feed deletion marker (e.g. SharePoint /delta)
                    result.deleted += self._delete_document(session, source.id, doc.external_id)
                    continue
                outcome, written = self._upsert_document(
                    session, project.id, source.id, doc, store_body=store_body
                )
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


@dataclass(slots=True)
class ReindexResult:
    source_id: str
    reindexed: int = 0  # documents re-chunked/re-embedded from stored body
    needs_refetch: int = 0  # documents with no stored body (marked stale for next sync)
    chunks: int = 0


def reindex(
    project_id: str, source_id: str | None = None, *, config: AppConfig | None = None
) -> list[ReindexResult]:
    """Rebuild chunks for a project with the CURRENT embedding model + chunk scheme,
    from each document's stored body (no provider re-fetch). Documents without a
    stored body are marked stale (content_hash cleared) so the next `sync` re-pulls
    and re-embeds them."""
    cfg = config or load_app_config()
    project = cfg.project(project_id)
    if project is None:
        raise ValueError(f"Unknown project '{project_id}'")
    source_ids = [s.id for s in project.sources]
    if source_id is not None:
        if source_id not in source_ids:
            raise ValueError(f"Unknown source '{source_id}' in project '{project_id}'")
        source_ids = [source_id]

    pipeline = IngestionPipeline(chunk=cfg.defaults.chunk)
    results: list[ReindexResult] = []
    for sid in source_ids:
        res = ReindexResult(source_id=sid)
        with get_session() as session:
            docs = session.scalars(
                select(Document).where(Document.source_id == sid)
            ).all()
            for doc in docs:
                n = pipeline.reindex_document(session, doc)
                if n < 0:  # no stored body → force re-fetch on next sync
                    doc.content_hash = ""
                    doc.embedding_model = ""
                    res.needs_refetch += 1
                else:
                    res.reindexed += 1
                    res.chunks += n
        results.append(res)
    return results
