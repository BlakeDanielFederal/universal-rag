# Architecture

Universal RAG ingests project context from multiple providers into a per-project
pgvector store and serves **project-scoped, citation-ready hybrid retrieval**.
Its responsibility ends at retrieval — **generation is the consuming client's
domain** (clients call `/query` and prompt their own model). There is no LLM
provider or renderer code in this repo.

## Component / data flow

```mermaid
flowchart TD
    subgraph Sources["Source providers"]
        CONF[Confluence<br/>roadmaps, specs]
        JIRA[Jira<br/>sprints, on-deck]:::future
        GH[GitHub<br/>PRs, releases]:::future
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
        CH[(chunks + embedding<br/>HNSW + FTS GIN)]
        SYNC[(sync_state cursors)]
    end

    subgraph Retrieval["Hybrid retrieval"]
        VEC[Semantic<br/>pgvector cosine]
        KW[Keyword<br/>Postgres FTS]
        RRF[Fuse RRF + optional rerank]
        HITS[Ranked, cited chunks<br/>JSON]
    end

    subgraph Interfaces["Interfaces (shared core)"]
        API[FastAPI REST<br/>projects / sync / query]
        MCP[FastMCP server<br/>list/sync/query_project]
        CLI[urag CLI]
    end

    CLIENT[Consuming client<br/>owns generation]:::future

    CONF & JIRA & GH & FUTURE --> BASE
    BASE --> CHUNK --> EMB --> CH
    BASE --> DOC
    PROJ --> SRC --> DOC --> CH
    SRC --> SYNC

    CH --> VEC & KW --> RRF --> HITS

    Interfaces --> Ingest
    Interfaces --> Retrieval
    HITS --> API & MCP --> CLIENT

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

## Retrieval surface (where this framework ends)

`HybridRetriever.search(query, project_id)` returns top-K grounded chunks, each
carrying `title` / `url` / `source_id` / `content` / `score`. That payload is
exposed verbatim over:

- **REST** — `POST /projects/{id}/query` (and `.../sync`, `GET /projects`)
- **MCP** — `query_project` / `sync_project` / `list_projects`
- **CLI** — `urag query <project> "<text>"`

A consuming client takes those cited chunks and does whatever it needs (draft an
email, memo, deck, answer a question). **Generation lives entirely on the client
side** — by design, this repo neither prompts an LLM nor renders artifacts.

## Build progress

- [x] `db-init` (tables + pgvector(768) + HNSW). Alembic + FTS/trgm DDL still TODO.
- [x] **Confluence Data Center connector** `fetch()` — CQL incremental, pagination,
      storage→text, PAT bearer auth. (Jira/GitHub still stubbed.)
- [x] Chunking + ingestion pipeline + embedder wiring (`run_sync`, hash-skip, cursor).
- [x] **Hybrid retrieval** — pgvector cosine + Postgres FTS, RRF fusion, optional
      rerank, project/source/metadata scoping (`HybridRetriever`, `urag query`).
- [x] **Retrieval surface over REST + MCP** — `projects` / `sync` / `query` routes
      and `list_projects` / `sync_project` / `query_project` tools.
- [x] **Generation removed** — out of scope; consuming clients own it.
- [ ] Jira + GitHub connectors. ← **next slices**
- [ ] SharePoint / OneDrive (MS Graph) connector.

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

### Hybrid retrieval detail (implemented)

```mermaid
flowchart LR
    Q[query] --> EQ[embed_query<br/>search_query:]
    EQ --> SEM[semantic arm<br/>pgvector cosine / HNSW]
    Q --> KW[keyword arm<br/>websearch_to_tsquery FTS / GIN]
    SCOPE[project_id + source_ids<br/>+ metadata filters] --> SEM
    SCOPE --> KW
    SEM --> RRF[Reciprocal Rank Fusion]
    KW --> RRF
    RRF --> RR{RERANK_MODEL?}
    RR -->|set| LLM[Ollama LLM judge]
    RR -->|unset| TOPK[top_k RetrievedChunks]
    LLM --> TOPK
```
