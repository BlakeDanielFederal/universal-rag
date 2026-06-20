"""Chunking. Two strategies:

- ``word`` (default): a sliding window over whitespace words (token counts are
  word-approximated to avoid a tokenizer dependency).
- ``sentence``: pack whole sentences into windows up to the word budget, with a
  sentence-level overlap — avoids cutting mid-sentence, which helps retrieval.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

_SENTENCE = re.compile(r"(?<=[.!?])\s+")


@dataclass(slots=True)
class TextChunk:
    ordinal: int
    content: str


def chunk_text(
    text: str, *, max_tokens: int = 512, overlap_tokens: int = 64, split: str = "word"
) -> list[TextChunk]:
    if max_tokens <= 0:
        raise ValueError("max_tokens must be positive")
    if split == "sentence":
        return _sentence_chunks(text, max_tokens, overlap_tokens)
    return _word_chunks(text, max_tokens, overlap_tokens)


def _word_chunks(text: str, max_tokens: int, overlap_tokens: int) -> list[TextChunk]:
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


def _sentence_chunks(text: str, max_tokens: int, overlap_tokens: int) -> list[TextChunk]:
    sentences = [s.strip() for s in _SENTENCE.split(text.strip()) if s.strip()]
    if not sentences:
        return []
    chunks: list[TextChunk] = []
    cur: list[str] = []
    cur_words = 0
    ordinal = 0
    for sent in sentences:
        w = len(sent.split())
        if cur and cur_words + w > max_tokens:
            chunks.append(TextChunk(ordinal=ordinal, content=" ".join(cur)))
            ordinal += 1
            # carry trailing sentences as overlap (~overlap_tokens words)
            keep: list[str] = []
            kept = 0
            for s in reversed(cur):
                sw = len(s.split())
                if kept + sw > overlap_tokens:
                    break
                keep.insert(0, s)
                kept += sw
            cur, cur_words = keep, kept
        cur.append(sent)
        cur_words += w
    if cur:
        chunks.append(TextChunk(ordinal=ordinal, content=" ".join(cur)))
    return chunks
