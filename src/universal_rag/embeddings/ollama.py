"""Ollama-backed embedder. Default model: nomic-embed-text (768-dim).

nomic-embed-text is asymmetric: documents and queries must be embedded with
different task prefixes ("search_document: " / "search_query: ") for good
retrieval. Prefixes come from settings and can be blanked for models that don't
use them. Keep the output dimension in sync with `db.models.EMBED_DIM`.
"""

from __future__ import annotations

import ollama

from universal_rag.config import get_settings


def _prefixed(prefix: str, text: str) -> str:
    if not prefix:
        return text
    return f"{prefix.rstrip()} {text}"


class OllamaEmbedder:
    def __init__(
        self,
        model: str | None = None,
        host: str | None = None,
        doc_prefix: str | None = None,
        query_prefix: str | None = None,
    ) -> None:
        settings = get_settings()
        self.model = model or settings.embedding_model
        self.doc_prefix = settings.embed_doc_prefix if doc_prefix is None else doc_prefix
        self.query_prefix = settings.embed_query_prefix if query_prefix is None else query_prefix
        self._client = ollama.Client(host=host or settings.ollama_host)

    def embed(self, texts: list[str]) -> list[list[float]]:
        """Embed raw texts with no prefix applied."""
        out: list[list[float]] = []
        for text in texts:
            resp = self._client.embeddings(model=self.model, prompt=text)
            out.append(list(resp["embedding"]))
        return out

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return self.embed([_prefixed(self.doc_prefix, t) for t in texts])

    def embed_query(self, text: str) -> list[float]:
        return self.embed([_prefixed(self.query_prefix, text)])[0]
