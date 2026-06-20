"""Contextual Retrieval (Anthropic): before embedding/indexing a chunk, prepend a
short LLM-written description situating it within its parent document. The
contextualized text is what gets embedded AND full-text indexed, so both retrieval
arms benefit. Uses a local Ollama chat model (no external API).
"""

from __future__ import annotations

_PROMPT = (
    "<document>\n{body}\n</document>\n\n"
    "Here is a chunk taken from the document above:\n<chunk>\n{chunk}\n</chunk>\n\n"
    "Give a short, succinct context (1-2 sentences) that situates this chunk within "
    "the overall document, to improve search retrieval of the chunk. Respond with "
    "ONLY the context and nothing else."
)
_BODY_CAP = 6000  # chars of parent doc included in the prompt
_CHUNK_CAP = 2000


class Contextualizer:
    def __init__(self, model: str, host: str | None = None) -> None:
        import ollama

        from universal_rag.config import get_settings

        self.model = model
        self._client = ollama.Client(host=host or get_settings().ollama_host)

    def context(self, body: str, chunk: str) -> str:
        """One situating sentence for `chunk` within `body`. '' on any failure."""
        prompt = _PROMPT.format(body=body[:_BODY_CAP], chunk=chunk[:_CHUNK_CAP])
        try:
            resp = self._client.chat(
                model=self.model,
                messages=[{"role": "user", "content": prompt}],
                options={"temperature": 0.0},
            )
            return resp["message"]["content"].strip()
        except Exception:
            return ""

    def contextualize(self, body: str, chunk: str) -> str:
        """Return the chunk prefixed with its context (or the bare chunk on failure)."""
        ctx = self.context(body, chunk)
        return f"{ctx}\n\n{chunk}" if ctx else chunk
