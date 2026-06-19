from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime

import pytest

from universal_rag.eval import metrics as M
from universal_rag.eval.dataset import GoldenItem, load_golden, save_golden


# --------------------------------------------------------------------------- #
# Pure metrics
# --------------------------------------------------------------------------- #
def test_retrieval_metrics_known_values() -> None:
    retrieved = [10, 20, 30, 40]
    relevant = {30}
    assert M.recall_at_k(retrieved, relevant, 2) == 0.0
    assert M.recall_at_k(retrieved, relevant, 3) == 1.0
    assert M.precision_at_k(retrieved, relevant, 3) == pytest.approx(1 / 3)
    assert M.reciprocal_rank(retrieved, relevant) == pytest.approx(1 / 3)
    # rank index 2 -> 1/log2(4)=0.5; ideal (1 relevant) = 1/log2(2)=1.0
    assert M.ndcg_at_k(retrieved, relevant, 3) == pytest.approx(0.5)
    assert M.ndcg_at_k(retrieved, relevant, 1) == 0.0  # relevant not in top-1


def test_metrics_no_relevant() -> None:
    assert M.recall_at_k([1, 2], set(), 5) == 0.0
    assert M.reciprocal_rank([1, 2], {9}) == 0.0


def test_golden_jsonl_roundtrip(tmp_path) -> None:
    items = [
        GoldenItem(
            query="what shipped?", project_id="p", relevant_doc_ids=["1", "2"], ideal_answer="x"
        ),
        GoldenItem(query="who owns auth?", project_id="p", relevant_doc_ids=["7"]),
    ]
    path = tmp_path / "golden.jsonl"
    save_golden(items, path)
    loaded = load_golden(path)
    assert loaded == items


# --------------------------------------------------------------------------- #
# Live runner (real pgvector + Ollama)
# --------------------------------------------------------------------------- #
PROJECT_ID = "itest_eval"
SOURCE_ID = f"{PROJECT_ID}-confluence"


def _services_up() -> bool:
    from universal_rag.db.session import get_engine

    try:
        get_engine().connect().close()
        from universal_rag.embeddings import OllamaEmbedder

        OllamaEmbedder().embed(["ping"])
        return True
    except Exception:
        return False


@pytest.mark.skipif(not _services_up(), reason="Postgres/Ollama not available")
def test_run_eval_scores_a_known_relevant_chunk(monkeypatch: pytest.MonkeyPatch) -> None:
    from universal_rag.config.schema import AppConfig, Defaults, ProjectConfig, SourceConfig
    from universal_rag.connectors.base import Connector, SourceDocument, SyncCursor
    from universal_rag.db.models import Project
    from universal_rag.db.session import get_session
    from universal_rag.eval.runner import run_eval
    from universal_rag.ingestion import run_sync

    class _Fake(Connector):
        provider = "confluence"

        def fetch(self, cursor: SyncCursor | None = None) -> Iterator[SourceDocument]:
            for ext, body in [
                ("1", "The analytics dashboard visualizes customer revenue trends. " * 8),
                ("2", "Authentication uses OAuth2 and short-lived JWT access tokens. " * 8),
            ]:
                yield SourceDocument(
                    source_id=self.source.id,
                    provider="confluence",
                    external_id=ext,
                    title=f"Doc {ext}",
                    content=body,
                    updated_at=datetime(2024, 3, 1, tzinfo=UTC),
                    metadata={},
                )

    monkeypatch.setattr("universal_rag.ingestion.pipeline.get_connector", lambda s: _Fake(s))
    config = AppConfig(
        defaults=Defaults(),
        projects=[
            ProjectConfig(
                id=PROJECT_ID,
                name="E",
                sources=[SourceConfig(id=SOURCE_ID, provider="confluence", spaces=["X"])],
            )
        ],
    )
    try:
        run_sync(PROJECT_ID, config=config)
        # Document "2" (auth) is the ground truth for an auth query — resolved to its
        # current chunks inside run_eval, so the golden survives re-index.
        golden = [
            GoldenItem(
                query="how do users authenticate and get tokens?",
                project_id=PROJECT_ID,
                relevant_doc_ids=["2"],
            )
        ]
        report = run_eval(golden, top_k=10)
        assert report.n == 1
        assert report.metrics["recall@10"] == 1.0  # a chunk from the auth doc is retrieved
        assert report.metrics["mrr"] > 0.0
    finally:
        with get_session() as s:
            proj = s.get(Project, PROJECT_ID)
            if proj is not None:
                s.delete(proj)
