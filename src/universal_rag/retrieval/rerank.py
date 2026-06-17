"""Optional reranking stage applied to the fused hybrid-retrieval candidates.

Default is a no-op (RRF order is already strong). When ``RERANK_MODEL`` is set we
use a local Ollama chat model as a lightweight relevance judge — useful when exact
ordering matters, at the cost of an extra local inference call. A true
cross-encoder can be slotted in later behind the same ``Reranker`` protocol.
"""

from __future__ import annotations

import json
import re
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from universal_rag.retrieval.hybrid import RetrievedChunk

_JSON_ARRAY = re.compile(r"\[.*\]", re.DOTALL)


class Reranker(Protocol):
    def rerank(self, query: str, candidates: list[RetrievedChunk]) -> list[RetrievedChunk]: ...


class NoopReranker:
    """Keeps the fused order unchanged."""

    def rerank(self, query: str, candidates: list[RetrievedChunk]) -> list[RetrievedChunk]:
        return candidates


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


def get_reranker(model: str | None = None) -> Reranker:
    from universal_rag.config import get_settings

    name = get_settings().rerank_model if model is None else model
    return OllamaReranker(name) if name else NoopReranker()
