"""Optional judge-based metric: does the retrieved context support the ideal answer?

Local Ollama judge by default (free, runs in CI without secrets). The `Judge`
protocol is the seam for a higher-fidelity hosted judge in offline runs.
"""

from __future__ import annotations

import json
import re
from typing import Protocol

_JSON = re.compile(r"\{.*\}", re.DOTALL)


class Judge(Protocol):
    def context_support(self, query: str, ideal_answer: str, contexts: list[str]) -> float: ...


class OllamaJudge:
    def __init__(self, model: str, host: str | None = None) -> None:
        import ollama

        from universal_rag.config import get_settings

        self.model = model
        self._client = ollama.Client(host=host or get_settings().ollama_host)

    def context_support(self, query: str, ideal_answer: str, contexts: list[str]) -> float:
        """1.0 if the judge says the retrieved context supports the answer, else 0.0."""
        joined = "\n---\n".join(c[:800] for c in contexts[:8])
        prompt = (
            "You evaluate retrieval quality. Given the QUERY, a reference ANSWER, and the "
            "retrieved CONTEXT passages, decide whether the context contains enough information "
            'to support the answer. Reply ONLY with JSON {"supported": true|false}.\n\n'
            f"QUERY: {query}\nANSWER: {ideal_answer}\nCONTEXT:\n{joined}"
        )
        try:
            resp = self._client.chat(
                model=self.model,
                messages=[{"role": "user", "content": prompt}],
                options={"temperature": 0.0},
            )
            match = _JSON.search(resp["message"]["content"])
            if match is None:
                return 0.0
            return 1.0 if json.loads(match.group(0)).get("supported") else 0.0
        except Exception:
            return 0.0


def get_judge() -> Judge | None:
    from universal_rag.config import get_settings

    name = get_settings().eval_judge_model
    return OllamaJudge(name) if name else None
