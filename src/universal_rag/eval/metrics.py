"""Pure retrieval metrics (no numpy, no DB). Binary relevance.

`retrieved` is the ranked list of chunk ids returned by the retriever (best first);
`relevant` is the ground-truth set of relevant chunk ids for the query.
"""

from __future__ import annotations

import math
from collections.abc import Sequence


def recall_at_k(retrieved: Sequence[int], relevant: set[int], k: int) -> float:
    if not relevant:
        return 0.0
    hit = len(set(retrieved[:k]) & relevant)
    return hit / len(relevant)


def precision_at_k(retrieved: Sequence[int], relevant: set[int], k: int) -> float:
    topk = retrieved[:k]
    if not topk:
        return 0.0
    return sum(1 for r in topk if r in relevant) / len(topk)


def reciprocal_rank(retrieved: Sequence[int], relevant: set[int]) -> float:
    for i, r in enumerate(retrieved):
        if r in relevant:
            return 1.0 / (i + 1)
    return 0.0


def ndcg_at_k(retrieved: Sequence[int], relevant: set[int], k: int) -> float:
    dcg = sum(1.0 / math.log2(i + 2) for i, r in enumerate(retrieved[:k]) if r in relevant)
    ideal = sum(1.0 / math.log2(i + 2) for i in range(min(len(relevant), k)))
    return dcg / ideal if ideal > 0 else 0.0
