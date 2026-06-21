# Eval results

Measured with the eval harness against **BeIR/fiqa** (financial Q&A, real qrels).
Reproduce: `urag eval import-beir BeIR/fiqa fiqa` then `urag eval run`. Corpus,
golden, and baselines are environment-local (gitignored); only these numbers are
committed.

## M1 — cross-encoder rerank (full fiqa, 200 test queries)

Retrieval-only (RRF) vs default cross-encoder rerank (`bge-reranker-v2-m3`):

| Metric | no rerank | + cross-encoder | Δ |
|---|---|---|---|
| MRR | 0.502 | 0.570 | **+13.5%** |
| nDCG@5 | 0.391 | 0.457 | **+16.7%** |
| nDCG@10 | 0.418 | 0.467 | +11.9% |
| recall@5 | 0.409 | 0.465 | +13.7% |
| recall@10 | 0.494 | 0.516 | +4.4% |
| recall@20 | 0.567 | 0.593 | +4.5% |

**Verdict: adopt (default on).** Gains concentrate on ordering (MRR/nDCG@5) as
expected — a reranker reorders the candidate pool, so recall@20 barely moves.
Cost: ~31 min for 200 queries on CPU (cap `--max-queries`; GPU/ONNX would help).

## M2 — Contextual Retrieval vs sliding (curated 2,523-doc subset, 200 queries, rerank off)

Contextualizer: `qwen2.5:1.5b` (0.89 s/call; ~39 min for the subset). gemma4:e4b
was 10.7 s/call → infeasible (~8 h for the subset, ~170 h for full fiqa).

| Metric | sliding | contextual | Δ |
|---|---|---|---|
| MRR | 0.794 | 0.781 | −1.6% |
| nDCG@5 | 0.673 | 0.681 | +1.1% |
| nDCG@10 | 0.707 | 0.698 | −1.2% |
| recall@5 | 0.666 | 0.674 | +1.1% |
| recall@10 | 0.768 | 0.743 | −3.3% |
| recall@20 | 0.829 | 0.822 | −0.8% |

**Verdict: no improvement on fiqa — keep opt-in (default off).** fiqa docs are
short (~130 words, ~1 chunk each), so the per-chunk context blurb just paraphrases
the chunk and adds no locating signal (and can dilute exact terms → recall@10 dip).
Contextual Retrieval targets **long, multi-chunk documents** (Confluence/SharePoint/
long READMEs); **re-test on a long-doc corpus before judging it.** It also carries a
real ingest cost (one generation call per chunk), so model choice is critical and a
fast small model (or GPU) is mandatory at scale.

## Notes
- Always run a 5-call latency probe before any large LLM-at-ingest job.
- Subset baselines sit higher than full-corpus (fewer distractors); compare deltas
  within a corpus, not absolute levels across corpora.
