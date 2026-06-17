"""Token-aware chunking with overlap.

TODO(impl): structure-aware splitting (headings/paragraphs) before falling back
to a sliding token window; keep `max_tokens`/`overlap_tokens` from config.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True)
class TextChunk:
    ordinal: int
    content: str


def chunk_text(text: str, *, max_tokens: int = 512, overlap_tokens: int = 64) -> list[TextChunk]:
    raise NotImplementedError("chunk_text not yet implemented")
