# Benchmark A - Retrieval Quality

Measures nDCG@5, Recall@10, and MRR of the mneme retrieval pipeline on a
deterministic synthetic corpus.

## Methodology

`mneme_core.bench.synth.build_synthetic_corpus(seed=42)` produces:

- **500 documents** across 10 generic technical topics (50 docs per topic).
  Each document has a unique two-term title pulled from a fixed
  10-term per-topic vocabulary. Bodies are stitched from the same
  vocabulary plus a shared filler list with a seeded RNG so two runs
  with the same seed produce byte-identical corpora.
- **50 queries** (5 per topic). Each query is anchored on a single
  target document's title pair. The relevant set for each query is
  exactly that one document; same-topic documents act as hard
  negatives, different-topic documents as easy negatives.

The corpus is materialized to a temp vault, indexed by the production
`mneme_core.fts5.indexer.index_vault`, and queried through the
production `mneme_core.retrieval.rrf.retrieve` pipeline.

## Conditions

| Condition | Pipeline | Purpose |
|---|---|---|
| `baseline_fts5_only` | Direct `fts5_search`, no min-query gate | Pure BM25 baseline, isolates the FTS5 leg. |
| `pipeline_fts5_only` | `retrieve` with FTS5 only | Validates the production wrapper matches the baseline. |
| `pipeline_rrf_fts5_plus_bow` | `retrieve` with FTS5 + BoW cosine | Exercises RRF fusion code path. |

The bag-of-words backend (`BowBackend` in `run.py`) is a pure-Python
TF-cosine surrogate. It is **not** a substitute for a packaged LEANN dense
adapter; it exists only so the benchmark can
exercise actual RRF fusion under controlled inputs.

## Metrics

- **nDCG@5**: binary-relevance DCG normalized by the ideal ranking.
- **Recall@10**: fraction of relevant docs (always 1 per query) in
  top-10. A query scores 1 if the target appears in top-10, else 0.
- **MRR**: mean of `1 / rank` of the relevant doc, with `cutoff=10`.

## Running

```bash
python benchmarks/retrieval/run.py --output-format=json --output result.json
```

Optional flags:

- `--seed 42` (default)
- `--docs-per-topic 50`
- `--queries-per-topic 5`
- `--hardware-output benchmarks/retrieval/hardware.json`
- `--output result.json`

## Regression guard

```bash
python benchmarks/retrieval/regression_guard.py result.json
```

Compares `conditions.pipeline_rrf_fts5_plus_bow.ndcg_at_5` against
`baseline.json` and exits non-zero if the metric drops by more than
0.02. CI calls this after every `run.py` invocation.

## Locked baseline (seed=42)

| Condition | nDCG@5 | Recall@10 | MRR |
|---|---|---|---|
| `baseline_fts5_only` | 0.801 | 1.000 | 0.734 |
| `pipeline_fts5_only` | 0.801 | 1.000 | 0.734 |
| `pipeline_rrf_fts5_plus_bow` | **0.893** | 1.000 | 0.671 |

Headline: **nDCG@5 = 0.893** with RRF fusion, up 9.2 points from the
single-leg FTS5 baseline. MRR drops because the BoW surrogate pulls
in cosine-similar same-topic neighbors that displace the target from
position 1; Recall@10 stays perfect because the target never leaves
the top-10.

## What this benchmark does not validate

- Real-world retrieval quality on operator vaults. Phase J's dogfood
  week is the source for that.
- The dense embedding leg. That is roadmap; this benchmark exercises a synthetic surrogate.
- Head-to-head versus claude-mem. See `benchmarks/head-to-head/`.
