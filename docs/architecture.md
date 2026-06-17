# Architecture

Universal RAG ingests project context from multiple providers into a per-project
pgvector store, then generates citation-grounded business communications.

## Component / data flow

```mermaid
flowchart TD
    subgraph Sources["Source providers"]
        CONF[Confluence<br/>roadmaps, specs]
        JIRA[Jira<br/>sprints, on-deck]
        GH[GitHub<br/>PRs, releases]
        FUTURE[SharePoint / OneDrive<br/>future]:::future
    end

    subgraph Connectors["Connectors (plugin registry)"]
        BASE[Connector ABC<br/>fetch since cursor → SourceDocument]
    end

    subgraph Ingest["Ingestion pipeline (incremental)"]
        CHUNK[Chunking<br/>token + overlap]
        EMB[OllamaEmbedder<br/>nomic-embed-text 768d]
    end

    subgraph Store["Postgres + pgvector"]
        PROJ[(projects)]
        SRC[(sources)]
        DOC[(documents)]
        CH[(chunks + embedding<br/>HNSW + FTS/trgm)]
        SYNC[(sync_state cursors)]
    end

    subgraph Retrieval["Hybrid retrieval"]
        VEC[Semantic<br/>pgvector cosine]
        KW[Keyword<br/>Postgres FTS]
        RRF[Fuse RRF + rerank]
    end

    subgraph Gen["Generation"]
        LLM{LLM provider<br/>auto-select}
        COP[GitHub Copilot CLI<br/>default]
        OLL[Ollama<br/>fallback]
        OUT[Outline JSON<br/>+ citations]
    end

    subgraph Render["Renderers"]
        MD[Markdown<br/>email/memo]
        PPTX[python-pptx<br/>.pptx]
        DOCX[python-docx<br/>.docx]
    end

    subgraph Interfaces["Interfaces (shared core)"]
        API[FastAPI REST]
        MCP[FastMCP server]
        CLI[urag CLI]
    end

    CONF & JIRA & GH & FUTURE --> BASE
    BASE --> CHUNK --> EMB --> CH
    BASE --> DOC
    PROJ --> SRC --> DOC --> CH
    SRC --> SYNC

    CH --> VEC & KW --> RRF --> LLM
    LLM -.prefers.-> COP
    LLM -.falls back.-> OLL
    LLM --> OUT --> MD & PPTX & DOCX

    Interfaces --> Ingest
    Interfaces --> Retrieval
    Interfaces --> Gen

    classDef future stroke-dasharray: 5 5,opacity:0.6;
```

## Project scoping

A **project workspace** (`projects` row) maps to one or more **sources**
(`sources` rows), each bound to a provider and its config (Confluence spaces,
Jira project keys, GitHub repos). Every `chunk` carries denormalized
`project_id` + `source_id`, so retrieval filters to a single project across all
providers in one indexed query — no cross-project bleed.

## Incremental sync

Each source has a `sync_state` cursor (typically a last-modified timestamp).
Ingestion fetches only items changed since the cursor, skips documents whose
`content_hash` is unchanged, re-embeds the rest, and advances the cursor.

## Generation flow

1. `HybridRetriever.search(query, project_id)` → top-K grounded chunks.
2. LLM (Copilot if available, else Ollama) produces a structured **`Outline`**
   with per-claim **citations** back to source chunks.
3. Renderers turn the one outline into Markdown / `.pptx` / `.docx`, keeping
   content and citations consistent across formats.

## Suggested build order

1. `db-init` + Alembic migration (adds FTS/trgm indexes raw DDL).
2. Connectors `fetch()` (start with one provider end-to-end).
3. Chunking + ingestion pipeline + embedder wiring.
4. Hybrid retrieval (semantic → +keyword → +rerank).
5. Generation service + Outline prompt; then renderers.
6. Flesh out FastAPI routers + FastMCP tools over the core.
```
