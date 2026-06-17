# Universal RAG

A **local-first RAG framework** that builds a per-project vector store from the
business systems where project knowledge actually lives — Confluence (roadmaps,
specs), Jira (what's on deck), GitHub (what's been implemented) — and uses it to
draft accurate, **citation-grounded** emails, memos, and presentations.

Different providers contribute different slices of context; Universal RAG unifies
them under a single project workspace so generated communications reflect the
whole picture.

## Design at a glance

| Concern | Choice |
| --- | --- |
| Language | Python 3.12 (managed with `uv`) |
| Vector store | **Postgres + pgvector** (vectors + metadata + sync state in one DB) |
| Embeddings | **Ollama** (`nomic-embed-text`, 768-dim) — fully local |
| Generation | Provider-agnostic; **GitHub Copilot CLI** default, **Ollama** fallback (no API keys required) |
| Retrieval | **Hybrid** (pgvector semantic + Postgres FTS) **+ rerank** |
| Project model | First-class **project workspaces**; every chunk tagged `project_id` + `source_id` |
| Sync | **Incremental polling** (per-source cursors) |
| Config | YAML for projects/sources, `.env` for secrets (Pydantic-validated) |
| Interfaces | **FastAPI** REST + **FastMCP** server (shared core) |
| Outputs | Markdown email/memo, `.pptx`, `.docx` from a shared JSON outline |
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

# 4. Pull local models (embeddings already needed; a chat model for fallback)
ollama pull nomic-embed-text
ollama pull qwen2.5-coder:14b-instruct

# 5. Create tables and explore the CLI
uv run urag db-init
uv run urag projects
uv run pytest
```

## Status

Scaffold + architecture in place. Connectors, ingestion, retrieval, generation,
and renderers are defined as interfaces with `NotImplementedError` stubs — see
`docs/architecture.md` for the component map and build order.
