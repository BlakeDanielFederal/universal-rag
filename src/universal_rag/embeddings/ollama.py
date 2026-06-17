"""Ollama-backed embedder. Default model: nomic-embed-text (768-dim).

Keep the output dimension in sync with `db.models.EMBED_DIM`.
"""

from __future__ import annotations

import ollama

from universal_rag.config import get_settings


class OllamaEmbedder:
    def __init__(self, model: str | None = None, host: str | None = None) -> None:
        settings = get_settings()
        self.model = model or settings.embedding_model
        self._client = ollama.Client(host=host or settings.ollama_host)

    def embed(self, texts: list[str]) -> list[list[float]]:
        """Embed a batch of texts. TODO: batching/concurrency for throughput."""
        out: list[list[float]] = []
        for text in texts:
            resp = self._client.embeddings(model=self.model, prompt=text)
            out.append(list(resp["embedding"]))
        return out

    def embed_one(self, text: str) -> list[float]:
        return self.embed([text])[0]
