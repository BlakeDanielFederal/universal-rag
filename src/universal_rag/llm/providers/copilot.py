"""GitHub Copilot provider (default when available).

Drives a locally-installed GitHub Copilot CLI as a subprocess so generation uses
the user's existing Copilot entitlement (no separate API key). Detects either the
newer agentic `copilot` binary or the `gh copilot` extension.

TODO(impl): pick the detected binary, send a non-interactive prompt, capture
stdout. Map `Message`s into a single prompt (system+context+instruction).
"""

from __future__ import annotations

import shutil
import subprocess

from universal_rag.llm.base import LLMProvider, Message


class CopilotProvider(LLMProvider):
    name = "github_copilot"

    def _binary(self) -> list[str] | None:
        if shutil.which("copilot"):
            return ["copilot"]
        if shutil.which("gh"):
            # `gh copilot` extension present?
            try:
                r = subprocess.run(["gh", "copilot", "--version"], capture_output=True, timeout=5)
                if r.returncode == 0:
                    return ["gh", "copilot"]
            except Exception:
                return None
        return None

    def available(self) -> bool:
        return self._binary() is not None

    def generate(self, messages: list[Message], *, temperature: float = 0.3) -> str:
        raise NotImplementedError("CopilotProvider.generate not yet implemented")
