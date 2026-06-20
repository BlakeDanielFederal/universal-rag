"""FastMCP server exposing this framework's indexing/retrieval core as tools.

An MCP client (e.g. Claude) lists projects, triggers ingestion, and queries
project-scoped context. The client decides what to *do* with the retrieved,
citation-ready chunks (draft an email, memo, deck, ...) — generation is not this
server's concern.

Run:  fastmcp run universal_rag.mcp.server:mcp
"""

from __future__ import annotations

from typing import Any

from fastmcp import FastMCP

mcp: FastMCP = FastMCP("universal-rag")


@mcp.tool
def list_projects() -> list[dict[str, str]]:
    """List configured project workspaces."""
    from universal_rag.config import load_app_config

    cfg = load_app_config()
    return [{"id": p.id, "name": p.name, "description": p.description} for p in cfg.projects]


@mcp.tool
def sync_project(project_id: str, source_id: str | None = None) -> list[dict[str, Any]]:
    """Ingest a project's sources into the vector store (all, or one source_id)."""
    from universal_rag.ingestion import run_sync

    return [vars(r) for r in run_sync(project_id, source_id)]


@mcp.tool
def query_project(
    project_id: str,
    query: str,
    top_k: int = 12,
    source_ids: list[str] | None = None,
    filters: dict[str, str] | None = None,
) -> list[dict[str, Any]]:
    """Hybrid-retrieve project-scoped chunks (semantic + keyword, RRF-fused, reranked).

    `filters` scopes by chunk metadata (e.g. {"status": "Done"} for Jira, {"space":
    "APOLLO"} for Confluence). Returns ranked, citation-ready chunks (title, url,
    source_id, content, score). The caller composes any downstream output from these.
    """
    from universal_rag.retrieval import HybridRetriever

    hits = HybridRetriever(top_k=top_k).search(
        query, project_id, source_ids=source_ids, filters=filters
    )
    return [
        {
            "chunk_id": h.chunk_id,
            "document_id": h.document_id,
            "source_id": h.source_id,
            "title": h.title,
            "url": h.url,
            "content": h.content,
            "score": h.score,
            "metadata": h.metadata,
        }
        for h in hits
    ]
