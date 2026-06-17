"""FastAPI app factory.

Planned routes (share the same core as the MCP server):
  GET  /healthz
  GET  /projects                         list configured projects
  POST /projects/{id}/sync               trigger ingestion (all/one source)
  POST /projects/{id}/query              hybrid retrieval (debug/inspection)
  POST /projects/{id}/generate           {prompt, artifact_type} -> outline/artifact
"""

from __future__ import annotations

from fastapi import FastAPI

from universal_rag import __version__


def create_app() -> FastAPI:
    app = FastAPI(title="Universal RAG", version=__version__)

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        return {"status": "ok", "version": __version__}

    # TODO: mount project/sync/query/generate routers.
    return app


app = create_app()
