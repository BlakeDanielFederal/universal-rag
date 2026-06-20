# Evaluation harness

Measures **retrieval quality** so every change to chunking, embeddings, or ranking
is proven, not assumed. Code lives in `src/universal_rag/eval/`; this dir holds the
golden set + baseline data.

## Workflow

```bash
# 1. Ingest a project (the corpus you'll evaluate against)
uv run urag sync <project>

# 2. Synthesize a golden set (LLM writes questions from your chunks; spot-check it)
EVAL_GEN_MODEL=qwen2.5-coder:14b-instruct uv run urag eval gen <project> --n 30 --out eval/golden.jsonl

# 3. Establish the baseline
uv run urag eval run --update-baseline

# 4. After any retrieval/chunking/embedding change, re-run — it fails on regression
uv run urag eval run
```

## Or: import a labeled benchmark (BEIR) as the testing corpus

Instead of synthesizing a golden set, import a standard retrieval benchmark with
**real human relevance judgments** (qrels). Recommended: **BeIR/fiqa** (~58K
financial docs):

```bash
uv run urag eval import-beir BeIR/fiqa fiqa            # ingest corpus + write golden.jsonl
RERANK_BACKEND=none uv run urag eval run --max-queries 200 --update-baseline   # retrieval-only baseline
uv run urag eval run --max-queries 200                 # default (cross-encoder rerank) vs baseline
```

`import-beir` ingests the corpus directly (no connector; `external_id` = corpus
`_id`) and converts queries+qrels → `golden.jsonl` (`relevant_doc_ids` = qrels).
A BEIR project supports `urag reindex` (rebuilds from stored bodies) but not
`urag sync` (provider `beir` has no connector). Cap `--max-queries` (~200) to keep
the cross-encoder rerank-eval practical on CPU.

## Metrics

- **recall@k, nDCG@k, MRR** — pure, deterministic; the primary gate.
- **context_support** — optional judge metric (set `EVAL_JUDGE_MODEL` to a local
  Ollama chat model; a hosted judge can be plugged via the `Judge` protocol).

## Golden-set format (`golden.jsonl`, one JSON object per line)

```json
{"query": "...", "project_id": "apollo", "relevant_doc_ids": ["APOL-42"], "ideal_answer": "..."}
```

Relevance is recorded at the **document** level (source `external_id`s) and resolved
to current chunk ids at eval time, so a golden set **survives re-chunking /
re-embedding / re-index** (chunk ids are not stable).

## Files

- `golden.jsonl` — the labeled query set (generated + human-curated; commit it).
- `baseline.json` — committed metric baseline; `eval run` fails when a metric drops
  more than the tolerance below it.

Both are corpus-specific; regenerate the baseline (`--update-baseline`) whenever the
golden set or the intended index signature changes.
