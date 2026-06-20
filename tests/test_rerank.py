from __future__ import annotations

import pytest

from universal_rag.config import get_settings
from universal_rag.retrieval import (
    CrossEncoderReranker,
    NoopReranker,
    OllamaReranker,
    RetrievedChunk,
    get_reranker,
)


def _chunk(cid: int, content: str) -> RetrievedChunk:
    return RetrievedChunk(
        chunk_id=cid,
        document_id=cid,
        project_id="p",
        source_id="s",
        content=content,
        title="",
        url="",
        score=0.0,
    )


def _reranker_for(monkeypatch: pytest.MonkeyPatch, **env: str):
    for key, val in env.items():
        monkeypatch.setenv(key, val)
    get_settings.cache_clear()
    try:
        return get_reranker()
    finally:
        get_settings.cache_clear()  # don't leak settings to other tests


def test_get_reranker_dispatch(monkeypatch: pytest.MonkeyPatch) -> None:
    assert isinstance(_reranker_for(monkeypatch, RERANK_BACKEND="none"), NoopReranker)
    assert isinstance(
        _reranker_for(monkeypatch, RERANK_BACKEND="cross_encoder"), CrossEncoderReranker
    )
    assert isinstance(
        _reranker_for(monkeypatch, RERANK_BACKEND="ollama", RERANK_MODEL="x"), OllamaReranker
    )


def test_cross_encoder_missing_model_falls_back_to_fused_order() -> None:
    rr = CrossEncoderReranker(model="definitely/not-a-real-model-xyz")
    cands = [_chunk(1, "a"), _chunk(2, "b")]
    assert rr.rerank("q", cands) == cands  # load fails -> RRF order preserved


def test_cross_encoder_reorders_by_relevance() -> None:
    rr = CrossEncoderReranker()
    if rr._load() is None:
        pytest.skip("cross-encoder model unavailable (offline)")
    cands = [
        _chunk(1, "Bananas are a good source of dietary potassium and fiber."),
        _chunk(2, "The capital of France is Paris, on the river Seine."),
    ]
    out = rr.rerank("What is the capital of France?", cands)
    assert out[0].chunk_id == 2  # the relevant passage is promoted to the top
    assert out[0].score >= out[1].score  # cross-encoder score is surfaced + ordered
