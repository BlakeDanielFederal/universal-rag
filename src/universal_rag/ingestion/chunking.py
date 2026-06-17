"""Sliding-window chunking with overlap.

Token counts are approximated by whitespace words (~1 token per word for English
prose) to avoid a heavyweight tokenizer dependency. The contract is stable, so a
real tokenizer (e.g. tiktoken / model-specific) can be dropped in later without
touching callers.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True)
class TextChunk:
    ordinal: int
    content: str


def chunk_text(text: str, *, max_tokens: int = 512, overlap_tokens: int = 64) -> list[TextChunk]:
    if max_tokens <= 0:
        raise ValueError("max_tokens must be positive")
    overlap = max(0, min(overlap_tokens, max_tokens - 1))

    words = text.split()
    if not words:
        return []

    step = max_tokens - overlap
    chunks: list[TextChunk] = []
    for ordinal, start in enumerate(range(0, len(words), step)):
        window = words[start : start + max_tokens]
        if not window:
            break
        chunks.append(TextChunk(ordinal=ordinal, content=" ".join(window)))
        if start + max_tokens >= len(words):
            break  # last window already reached the end
    return chunks
