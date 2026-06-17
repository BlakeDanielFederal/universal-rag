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

## Build progress

- [x] `db-init` (tables + pgvector(768) + HNSW). Alembic + FTS/trgm DDL still TODO.
- [x] **Confluence Data Center connector** `fetch()` — CQL incremental, pagination,
      storage→text, PAT bearer auth. (Jira/GitHub still stubbed.)
- [x] Chunking + ingestion pipeline + embedder wiring (`run_sync`, hash-skip, cursor).
- [ ] Hybrid retrieval (semantic → +keyword → +rerank). ← **next slice**
- [ ] Generation service + Outline prompt; then renderers.
- [ ] FastAPI routers + FastMCP tools over the core (query + generate).

### Confluence ingestion detail (implemented)

```mermaid
flowchart LR
    CFG[config.yaml<br/>source.spaces] --> CONN
    ENV[.env<br/>CONFLUENCE_BASE_URL + PAT] --> CONN
    ST[(sync_state.cursor)] --> CONN[ConfluenceConnector]
    CONN -->|CQL: space + lastmodified ≥ cursor| API[Confluence DC REST<br/>Bearer PAT]
    API -->|paged content| PARSE[page_to_document<br/>storage XHTML → text]
    PARSE --> HASH{content_hash<br/>changed?}
    HASH -->|no| SKIP[skip]
    HASH -->|yes| CK[chunk_text] --> EMB[embed_documents<br/>search_document:] --> UP[(upsert Document + Chunks)]
    UP --> ADV[advance cursor = max updated_at]
    ADV --> ST
```
```
