"""Retrieval evaluation harness (offline; not on the retrieval core path).

- `dataset` — golden-set JSONL (query → relevant chunks + ideal answer)
- `metrics` — pure retrieval metrics (recall@k, precision@k, MRR, nDCG@k)
- `judge`   — optional judge-based context-support metric (local Ollama default)
- `generate`— synthetic golden-set generation from ingested chunks
- `runner`  — drives HybridRetriever over a golden set, aggregates, gates on baseline
"""

from universal_rag.eval.dataset import GoldenItem, load_golden, save_golden
from universal_rag.eval.runner import EvalReport, compare_baseline, run_eval

__all__ = [
    "EvalReport",
    "GoldenItem",
    "compare_baseline",
    "load_golden",
    "run_eval",
    "save_golden",
]
