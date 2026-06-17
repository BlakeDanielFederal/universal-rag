"""Ollama provider — the zero-config local fallback for generation.

Uses OLLAMA_GENERATION_MODEL (any installed chat model, e.g. qwen2.5-coder).
"""

from __future__ import annotations

import ollama

from universal_rag.config import get_settings
from universal_rag.llm.base import LLMProvider, Message


class OllamaProvider(LLMProvider):
    name = "ollama"

    def __init__(self, model: str | None = None) -> None:
        settings = get_settings()
        self.model = model or settings.ollama_generation_model
        self._client = ollama.Client(host=settings.ollama_host)

    def available(self) -> bool:
        try:
            self._client.list()
            return True
        except Exception:
            return False

    def generate(self, messages: list[Message], *, temperature: float = 0.3) -> str:
        resp = self._client.chat(
            model=self.model,
            messages=[{"role": m.role, "content": m.content} for m in messages],
            options={"temperature": temperature},
        )
        return resp["message"]["content"]
