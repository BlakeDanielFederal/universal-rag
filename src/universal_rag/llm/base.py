"""LLM provider contract for draft generation.

Providers are interchangeable. The factory picks one per the LLM_PROVIDER
setting; `auto` prefers GitHub Copilot CLI when present and falls back to Ollama.
"""

from __future__ import annotations

import abc
from dataclasses import dataclass


@dataclass(slots=True)
class Message:
    role: str  # system | user | assistant
    content: str


class LLMProvider(abc.ABC):
    name: str

    @abc.abstractmethod
    def available(self) -> bool:
        """True if this provider can run on this machine right now."""
        raise NotImplementedError

    @abc.abstractmethod
    def generate(self, messages: list[Message], *, temperature: float = 0.3) -> str:
        raise NotImplementedError
