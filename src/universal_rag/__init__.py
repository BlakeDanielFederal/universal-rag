"""Universal RAG — multi-provider, project-scoped indexing/storage/retrieval.

Pipeline:  connectors -> ingestion (chunk + embed) -> pgvector store
           -> hybrid retrieval (+ optional rerank) -> ranked, cited chunks.

Exposed over a FastAPI REST API and a FastMCP server sharing one core library.
Generation is out of scope — consuming clients retrieve and prompt their own model.
"""

__version__ = "0.1.0"
