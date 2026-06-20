"""Reranking stage applied to the fused hybrid-retrieval candidates.

Default backend is a self-hosted **cross-encoder** (sentence-transformers
``bge-reranker-v2-m3``) — it scores each (query, passage) pair jointly, which is
markedly more accurate than the RRF order alone. Alternatives behind the same
``Reranker`` protocol: an Ollama LLM-judge, or a no-op (RRF order as-is). All are
local-first / self-hostable.
"""

from __future__ import annotations

import json
import re
from typing import TYPE_CHECKING, Protocol

import structlog

if TYPE_CHECKING:
    from universal_rag.retrieval.hybrid import RetrievedChunk

log = structlog.get_logger("universal_rag.rerank")
_JSON_ARRAY = re.compile(r"\[.*\]", re.DOTALL)


class Reranker(Protocol):
    def rerank(self, query: str, candidates: list[RetrievedChunk]) -> list[RetrievedChunk]: ...


class NoopReranker:
    """Keeps the fused order unchanged."""

    def rerank(self, query: str, candidates: list[RetrievedChunk]) -> list[RetrievedChunk]:
        return candidates


class CrossEncoderReranker:
    """Self-hosted cross-encoder (sentence-transformers). Lazy-loads the model on
    first use; if torch/the model can't be loaded, degrades to the fused order."""

    def __init__(self, model: str = "BAAI/bge-reranker-v2-m3") -> None:
        self.model = model
        self._encoder = None  # lazy
        self._broken = False

    def _load(self):  # noqa: ANN202 - sentence_transformers types are optional
        if self._encoder is None and not self._broken:
            try:
                from sentence_transformers import CrossEncoder

                self._encoder = CrossEncoder(self.model)
            except Exception as exc:
                self._broken = True
                log.warning("rerank.load_failed", model=self.model, error=str(exc))
        return self._encoder

    def rerank(self, query: str, candidates: list[RetrievedChunk]) -> list[RetrievedChunk]:
        if not candidates:
            return candidates
        encoder = self._load()
        if encoder is None:
            return candidates  # no model -> trust RRF
        try:
            scores = encoder.predict([(query, c.content) for c in candidates])
        except Exception as exc:
            log.warning("rerank.predict_failed", error=str(exc))
            return candidates
        ranked = sorted(zip(candidates, scores, strict=True), key=lambda cs: cs[1], reverse=True)
        out: list[RetrievedChunk] = []
        for chunk, score in ranked:
            chunk.score = float(score)  # surface the cross-encoder score to clients
            out.append(chunk)
        return out


class OllamaReranker:
    """Best-effort LLM relevance scoring; falls back to fused order on any error."""

    def __init__(self, model: str, host: str | None = None) -> None:
        import ollama

        from universal_rag.config import get_settings

        self.model = model
        self._client = ollama.Client(host=host or get_settings().ollama_host)

    def rerank(self, query: str, candidates: list[RetrievedChunk]) -> list[RetrievedChunk]:
        if not candidates:
            return candidates
        passages = "\n".join(
            f"[{i}] {c.title}: {c.content[:500]}" for i, c in enumerate(candidates)
        )
        prompt = (
            "Score how well each passage answers the query on a 0-10 scale. "
            'Reply ONLY with a JSON array of {"i": <index>, "score": <number>}.\n\n'
            f"Query: {query}\n\nPassages:\n{passages}"
        )
        try:
            resp = self._client.chat(
                model=self.model,
                messages=[{"role": "user", "content": prompt}],
                options={"temperature": 0.0},
            )
            match = _JSON_ARRAY.search(resp["message"]["content"])
            if match is None:
                return candidates
            scores = {int(item["i"]): float(item["score"]) for item in json.loads(match.group(0))}
        except Exception:
            return candidates  # judge failed — trust RRF
        order = sorted(range(len(candidates)), key=lambda i: scores.get(i, 0.0), reverse=True)
        return [candidates[i] for i in order]


def get_reranker() -> Reranker:
    """Resolve the configured reranker. Default: cross-encoder (self-hosted)."""
    from universal_rag.config import get_settings

    s = get_settings()
    backend = s.rerank_backend
    if backend == "cross_encoder":
        return CrossEncoderReranker(s.rerank_model or "BAAI/bge-reranker-v2-m3")
    if backend == "ollama" and s.rerank_model:
        return OllamaReranker(s.rerank_model)
    return NoopReranker()
