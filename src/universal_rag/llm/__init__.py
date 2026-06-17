"""LLM provider selection.

Default chain (LLM_PROVIDER=auto): GitHub Copilot CLI -> Ollama.
Copilot is preferred when its CLI is installed/authenticated; otherwise we fall
back to a local Ollama chat model so the framework works with zero API keys.
"""

from __future__ import annotations

from universal_rag.config import get_settings
from universal_rag.llm.base import LLMProvider, Message
from universal_rag.llm.providers.copilot import CopilotProvider
from universal_rag.llm.providers.ollama import OllamaProvider


def get_provider(name: str | None = None) -> LLMProvider:
    """Resolve a provider by name, or auto-select the first available one."""
    choice = name or get_settings().llm_provider

    if choice == "github_copilot":
        return CopilotProvider()
    if choice == "ollama":
        return OllamaProvider()

    # auto: prefer Copilot, fall back to Ollama.
    copilot = CopilotProvider()
    if copilot.available():
        return copilot
    return OllamaProvider()


__all__ = ["LLMProvider", "Message", "get_provider"]
