"""FastAPI app factory.

This service's job ends at retrieval: it indexes provider content into a
per-project vector store and serves project-scoped hybrid search. Generation
(emails, memos, decks, ...) is the consuming client's responsibility — clients
call /query, get citation-ready chunks, and prompt their own model.

Routes (shared core with the MCP server):
  GET  /healthz
  GET  /projects                 list configured projects
  POST /projects/{id}/sync       trigger ingestion (all sources or ?source=ID)
  POST /projects/{id}/query      hybrid retrieval -> ranked, cited chunks
"""

from __future__ import annotations

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from universal_rag import __version__


class QueryRequest(BaseModel):
    query: str
    top_k: int = 12
    candidate_k: int | None = None  # pool size per arm before fusion/rerank
    rerank: bool = True  # set false to skip the cross-encoder and return fused order
    source_ids: list[str] | None = None
    filters: dict[str, str] | None = None


class RetrievedChunkModel(BaseModel):
    chunk_id: int
    document_id: int
    project_id: str
    source_id: str
    title: str
    url: str
    content: str
    score: float
    metadata: dict = Field(default_factory=dict)


class QueryResponse(BaseModel):
    project_id: str
    query: str
    results: list[RetrievedChunkModel]


class SyncResultModel(BaseModel):
    source_id: str
    documents: int
    chunks: int
    skipped: int
    status: str
    error: str = ""


def create_app() -> FastAPI:
    app = FastAPI(
        title="Universal RAG",
        version=__version__,
        summary="Project-scoped indexing, storage, and hybrid retrieval. Clients own generation.",
    )

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        return {"status": "ok", "version": __version__}

    @app.get("/projects")
    def list_projects() -> list[dict[str, str]]:
        from universal_rag.config import load_app_config

        cfg = load_app_config()
        return [{"id": p.id, "name": p.name, "description": p.description} for p in cfg.projects]

    @app.post("/projects/{project_id}/sync")
    def sync(project_id: str, source: str | None = None) -> list[SyncResultModel]:
        from universal_rag.ingestion import run_sync

        try:
            results = run_sync(project_id, source)
        except ValueError as exc:  # unknown project/source
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return [SyncResultModel(**vars(r)) for r in results]

    @app.post("/projects/{project_id}/query")
    def query(project_id: str, req: QueryRequest) -> QueryResponse:
        from universal_rag.retrieval import HybridRetriever, NoopReranker

        retriever = HybridRetriever(
            top_k=req.top_k,
            candidate_k=req.candidate_k,
            reranker=None if req.rerank else NoopReranker(),
        )
        hits = retriever.search(
            req.query, project_id, source_ids=req.source_ids, filters=req.filters
        )
        results = [
            RetrievedChunkModel(
                chunk_id=h.chunk_id,
                document_id=h.document_id,
                project_id=h.project_id,
                source_id=h.source_id,
                title=h.title,
                url=h.url,
                content=h.content,
                score=h.score,
                metadata=h.metadata,
            )
            for h in hits
        ]
        return QueryResponse(project_id=project_id, query=req.query, results=results)

    return app


app = create_app()
