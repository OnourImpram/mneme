# Mneme 3.6 Benchmark Gate

## Status

The Mneme 3.6 gate exposes seven independently runnable benchmark surfaces from
`mneme_core.bench.gate`. Every bundled input is synthetic. The default corpus is
generated with seed 42 and contains 500 documents and 50 queries. Timing and
memory values are measured during each run. No measured result is hard coded in
the implementation or this document.

The gate is a regression and contract surface. It is not evidence that Mneme
outperforms another product, and it does not turn feature hashing into semantic
or dense model retrieval.

## Run

Run all seven surfaces after installing `mneme-core` in the active environment.

```console
python -m mneme_core.bench.gate all \
  --output benchmarks/_runs/mneme-3.6-gate.json
```

Run one surface by replacing `all` with its name.

```console
python -m mneme_core.bench.gate retrieval-quality
python -m mneme_core.bench.gate retrieval-latency
python -m mneme_core.bench.gate index-build
python -m mneme_core.bench.gate memory-footprint
python -m mneme_core.bench.gate backend-contribution-ablation
python -m mneme_core.bench.gate longmemeval-schema
python -m mneme_core.bench.gate locomo-schema
```

The process exits with code 0 only when the selected gate passes. Use
`--docs-per-topic`, `--queries-per-topic`, `--cutoff`, `--latency-samples`, and
`--latency-budget-ms` to change the local run profile. Reduced profiles are
appropriate for smoke tests. A reduced profile must not be reported as the
default 500 document gate.

The retrieval-quality surface identifies its executed path as
`python-core-production-fts5`. At the default cutoff of 10 it requires
Recall@10 at or above 0.95, Precision@10 at or above 0.09, MRR at or above
0.65, and nDCG@10 at or above 0.70. The JSON output records these thresholds
next to the measured metrics.

## Surfaces

| Surface | Code path | Reported evidence |
| --- | --- | --- |
| `retrieval-quality` | Shipped production FTS5 path only | Recall at K, Precision at K, MRR, nDCG at K |
| `retrieval-latency` | Shipped FTS5 retrieval path after warmup | p50, p95, p99, result count, sample count, budget |
| `index-build` | Production FTS5 schema and vault indexer | Build time, documents per second, index size, indexed and failed counts |
| `memory-footprint` | FTS5 build, feature hash index materialization, and fused retrieval | Python current allocation, peak allocation, peak delta, materialized document count |
| `backend-contribution-ablation` | FTS5 only, feature hash only, and fused RRF conditions | All retrieval metrics, backend output contribution, relevant contribution, unique contribution, condition deltas |
| `longmemeval-schema` | Strict LongMemEval adapter plus production FTS5 over an invented contract fixture | Schema acceptance, malformed record rejection, retrieval metrics |
| `locomo-schema` | Strict nested LoCoMo adapter plus production FTS5 over an invented contract fixture | Schema acceptance, QA flattening, malformed record rejection, retrieval metrics |

## Output Contract

Every surface includes a `provenance` object with these fields.

1. `dataset_kind` is `synthetic`.
2. `synthetic` is `true`.
3. `deterministic_input` is `true`.
4. `seed` records the generator seed.
5. `result_kind` is `local-measurement`.

The aggregate payload uses schema version `mneme-benchmark-gate/1`, reports a
surface count of seven, and sets `passed` only when every surface passes. JSON
serialization rejects NaN and Infinity. The gate does not write a result file
unless the operator explicitly supplies `--output`.

CLI output also embeds the operating system, CPU, logical core count, Python
version, Node version when available, and benchmark seed under `hardware`.
The exact seed, corpus sizing, cutoff, latency sample count, and latency budget
are preserved under `configuration`.

## Metric Definitions

Recall at K is the number of distinct relevant documents returned in the first
K positions divided by the number of relevant documents.

Precision at K uses a fixed K denominator. Missing positions in a short result
list count as misses. Duplicate relevant document identifiers count once.

MRR is the reciprocal rank of the first relevant result, averaged across
queries, with the configured cutoff applied.

nDCG at K uses binary relevance. Each relevant document can contribute once,
so duplicate identifiers cannot inflate the score.

p95 uses linear interpolation over measured wall clock samples. The default
retrieval budget is strictly below 1000 milliseconds. This core surface does
not measure the Claude Stop hook. The existing Stop hook benchmark remains a
separate integration gate.

Index size is the total size of the SQLite database and matching sidecar files
that remain after the indexing connection closes. Build time is local wall
clock time and is not portable across hardware.

Memory is measured with Python `tracemalloc`. It does not include SQLite native
page allocations, model runtimes, operating system cache, or total process RSS.

Backend contribution counts result provenance. It is descriptive, not causal.
Feature hash values are always labelled `feature_hash_lexical`. They are not
reported as semantic embeddings or as a shipped dense backend.

## Official Schema Contracts

`load_longmemeval_official` follows the public
[LongMemEval dataset format](https://github.com/xiaowu0162/LongMemEval/tree/9e0b455f4ef0e2ab8f2e582289761153549043fc#dataset-format).
It validates question metadata, aligned haystack arrays, turn roles and content,
evidence session identifiers, duplicate identifiers, and internal references.
Official abstention records ending in `_abs` are validated but excluded from
retrieval cases by default because they have no evidence session. Set
`include_abstention=True` only for a separately reported abstention analysis.

`load_locomo_official` follows the public
[LoCoMo data format](https://github.com/snap-research/locomo/tree/3eb6f2c585f5e1699204e3c3bdf7adc5c28cb376#data). It validates
conversation speakers, numbered sessions and dates, dialog identifiers, nested
QA annotations, categories, and evidence references. It emits one retrieval
case for each nested QA annotation.

The gate constructs small invented records with the same public field shapes.
It does not download, copy, or embed either official dataset. Metrics from the
two schema surfaces are contract probe measurements. They must never be cited
as LongMemEval or LoCoMo benchmark scores.

## Pass Criteria

1. Retrieval metrics exist, are finite, remain within 0 and 1, evaluate at least one query, and meet the recorded minimums. At the default cutoff these are Recall@10 0.95, Precision@10 0.09, MRR 0.65, and nDCG@10 0.70.
2. Retrieval latency has at least one sample and p95 is below the configured budget.
3. Every expected synthetic document is indexed, no index error is recorded, and build time and index size are positive.
4. The measured Python peak delta is positive and the feature hash index contains every synthetic document.
5. All three ablation conditions produce finite metrics and backend contribution is reported.
6. The LongMemEval strict adapter accepts the valid contract fixture and rejects a malformed fixture.
7. The LoCoMo strict adapter accepts the valid nested contract fixture and rejects a malformed fixture.

## Interpretation Limits

Input determinism does not make wall clock or memory measurements identical
across machines. Publish hardware, operating system, Python version, seed,
command, commit SHA, and the raw JSON whenever reporting a run.

Real LongMemEval or LoCoMo evaluation requires the operator to obtain the
datasets under their upstream terms and run a separately controlled study.
This gate intentionally makes no real dataset, semantic model, competitor, or
cross hardware superiority claim.
