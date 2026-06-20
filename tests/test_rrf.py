from __future__ import annotations

from universal_rag.retrieval import reciprocal_rank_fusion


def test_rrf_rewards_agreement_across_lists() -> None:
    semantic = [1, 2, 3]
    keyword = [3, 1, 4]
    scores = reciprocal_rank_fusion([semantic, keyword], k=60)

    # id 1: rank0 in semantic + rank1 in keyword -> highest combined
    # id 3: rank2 in semantic + rank0 in keyword
    assert max(scores, key=scores.__getitem__) == 1
    assert scores[1] > scores[3] > scores[2]
    assert scores[4] > 0  # appears once, still scored


def test_rrf_higher_rank_scores_more() -> None:
    scores = reciprocal_rank_fusion([[10, 20, 30]], k=60)
    assert scores[10] > scores[20] > scores[30]


def test_weighted_rrf_favors_the_weighted_arm() -> None:
    dense = [1, 2]
    sparse = [3, 4]
    # equal weights: the two rank-0 items tie
    eq = reciprocal_rank_fusion([dense, sparse])
    assert eq[1] == eq[3]
    # weight dense higher -> its rank-0 item outranks sparse's rank-0 item
    weighted = reciprocal_rank_fusion([dense, sparse], weights=[0.8, 0.2])
    assert weighted[1] > weighted[3]
