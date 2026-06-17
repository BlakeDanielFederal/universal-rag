"""Universal RAG — multi-provider, project-scoped RAG for business comms.

Pipeline:  connectors -> ingestion (chunk + embed) -> pgvector store
           -> hybrid retrieval (+ rerank) -> LLM generation -> renderers.

Exposed over a FastAPI REST API and a FastMCP server sharing one core library.
"""

__version__ = "0.1.0"
