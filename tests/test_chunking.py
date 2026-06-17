from __future__ import annotations

from universal_rag.ingestion.chunking import chunk_text


def test_empty_text_yields_no_chunks() -> None:
    assert chunk_text("") == []
    assert chunk_text("   \n  ") == []


def test_short_text_is_one_chunk() -> None:
    chunks = chunk_text("a short page about apollo", max_tokens=512, overlap_tokens=64)
    assert len(chunks) == 1
    assert chunks[0].ordinal == 0
    assert chunks[0].content == "a short page about apollo"


def test_windows_overlap_and_ordinals_are_sequential() -> None:
    words = [f"w{i}" for i in range(25)]
    chunks = chunk_text(" ".join(words), max_tokens=10, overlap_tokens=3)

    # step = 7 -> windows start at 0, 7, 14, 21
    assert [c.ordinal for c in chunks] == [0, 1, 2, 3]
    assert chunks[0].content.split() == words[0:10]
    assert chunks[1].content.split() == words[7:17]
    # overlap: last 3 words of chunk 0 reappear at the start of chunk 1
    assert chunks[0].content.split()[-3:] == chunks[1].content.split()[:3]
    # final window reaches the end exactly once
    assert chunks[-1].content.split()[-1] == "w24"
