# Universal RAG

A **local-first indexing, storage, and retrieval framework** that builds a
per-project vector store from the business systems where project knowledge
actually lives — Confluence (roadmaps, specs), Jira (what's on deck), GitHub
(what's been implemented), SharePoint/OneDrive (the wider business context),
plus arbitrary **websites** and **local directories** — and serves
**project-scoped, citation-ready hybrid retrieval** over REST and MCP.

Different providers contribute different slices of context; Universal RAG unifies
them under a single project workspace and hands a consuming client ranked,
sourced chunks. **Generation is deliberately out of scope** — clients call
`/query` (or the MCP `query_project` tool) and prompt their own model to draft
emails, memos, decks, or anything else. This project owns ingest → store →
retrieve; the client owns what comes after.

## Design at a glance

| Concern | Choice |
| --- | --- |
| Language | Python 3.12 (managed with `uv`) |
| Vector store | **Postgres + pgvector** (vectors + metadata + sync state in one DB) |
| Embeddings | **Ollama** (`nomic-embed-text`, 768-dim) — fully local, no API keys |
| Retrieval | **Hybrid** (pgvector semantic + Postgres FTS, RRF-fused) + optional rerank |
| Project model | First-class **project workspaces**; every chunk tagged `project_id` + `source_id` |
| Sync | **Incremental polling** (per-source cursors, hash-skip unchanged) |
| Config | YAML for projects/sources, `.env` for secrets (Pydantic-validated) |
| Interfaces | **FastAPI** REST + **FastMCP** server + `urag` CLI (shared core) |
| Output | Ranked, cited chunks (JSON) — **clients generate; this framework does not** |
| Packaging | **Docker Compose** for Postgres+pgvector; Ollama runs on host |

## Quickstart

```bash
# 1. Install deps into a local venv
uv sync --extra dev

# 2. Configure
cp .env.example .env                       # fill in connector tokens
cp config/config.example.yaml config/config.yaml

# 3. Stand up Postgres + pgvector
docker compose up -d

# 4. Pull the local embedding model
ollama pull nomic-embed-text

# 5. Create/upgrade the schema (runs Alembic migrations), ingest, and query
uv run urag db-init                         # = alembic upgrade head
uv run urag projects
uv run urag sync <project> --source <source-id>
uv run urag query <project> "what's on deck for next sprint?"
uv run pytest
```

To keep sources fresh on a cron cadence (from `config.yaml`'s `sync.schedule`):

```bash
uv run urag serve-scheduler   # in-process daemon; runs incremental syncs on schedule
```

## Consuming retrieval

Clients integrate over either interface and own generation downstream:

- **REST:** `POST /projects/{id}/query` → `{ results: [{title, url, source_id, content, score, ...}] }`
- **MCP:** `query_project(project_id, query, top_k=...)` → the same ranked, cited chunks

```bash
uv run urag serve-api    # FastAPI on :8000  (GET /projects, POST .../sync, POST .../query)
uv run urag serve-mcp    # FastMCP (stdio):  list_projects / sync_project / query_project
```

## Status

Implemented end-to-end: connectors for **Confluence DC**, **Jira DC**, **GitHub**,
**SharePoint/OneDrive** (Microsoft Graph `/delta`), **websites** (recursive crawl +
main-content extraction), and **local directories**; incremental ingestion into
pgvector with **deletion handling** (reconcile-prune, or delta tombstones for
SharePoint); Alembic migrations; a cron scheduler; hybrid retrieval; and the
REST/MCP query+sync surface. See `docs/architecture.md` for the component map.
