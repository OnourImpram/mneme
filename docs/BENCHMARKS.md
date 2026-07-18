# Benchmarks

mneme publishes seven reproducible benchmarks. Run them with `make bench-all` or individually as documented below.

## Reproducibility Contract

All benchmarks consume a deterministic synthetic corpus built from a fixed seed. Two runs with the same seed produce byte-identical input data.

```bash
export MNEME_BENCH_SEED=42   # default, set in Makefile
make bench-all               # writes to benchmarks/_runs/ (gitignored)
```

Each run also writes a `hardware.json` capturing CPU model, core count, RAM, OS, Python version, and Node version. This lets results be interpreted in context across operator hardware, CI runners, and contributor machines.

**Corrected for 3.6.0 (2026-07-18, seed 42):** Benchmark A now guards the shipped production FTS5 path at nDCG@5 0.801, Recall@10 1.0, and MRR 0.734. The former fused 0.893 value was invalid because different FTS5 and BoW identifier domains allowed one relevant document to be counted twice by the old metric. Duplicate-safe nDCG and canonical cross-backend identifiers expose the BoW condition as a degrading lexical-surrogate ablation. It is not a semantic or dense result.

## Methodology

All benchmarks pin hardware specs in a `hardware.json` file alongside results. CI uses GitHub Actions ubuntu-latest runners (2 vCPU, 7 GB RAM). Developer machines may show different absolute numbers but consistent relative deltas. CI regression guards lock the deltas, not the absolutes.

## Benchmark A: Retrieval Quality

Synthetic 500-document corpus with 50 title-anchored queries and hard negatives. Metrics: nDCG@5, Recall@10, MRR.

Conditions compared:

- `pipeline_rrf_fts5_plus_bow`, shipped RRF code path with FTS5 plus a deterministic BoW surrogate.
- `pipeline_fts5_only`, production wrapper with the FTS5 leg only.
- `baseline_fts5_only`, direct BM25 baseline.

The BoW leg is a lexical-surrogate ablation for fusion plumbing. It is not a shipped semantic or dense adapter and is not the quality headline.

```bash
make bench-retrieval
```

CI regression guard: nDCG@5 drop of more than 2 points fails the build.

## Benchmark B: Latency

Stop hook p50/p95/p99 over 100 sessions. SessionStart retrieval p95. Vault rebuild docs per second at 1k, 3k, 7k corpus sizes.

```bash
make bench-latency
```

CI regression guard: Stop hook p95 must stay under 1000 ms.

## Benchmark C: Token Cost

Token-saving primitives measured independently across deterministic fixtures.

```bash
make bench-cost
```

## Benchmark D: Migration Validation

Synthetic claude-mem SQLite fixture, migration CLI invocation, idempotence check, dedup check, and redaction invariant check.

```bash
make bench-migration
```

Target: all structural assertions pass.

## Benchmark E: Head-to-Head

Run the shared adapter harness on the default synthetic fixture. `MnemeAdapter` always runs when `npx` is available. `ClaudeMemAdapter` is gated by `CLAUDE_MEM_BIN` or a real `claude-mem` binary on PATH. Measure:

1. Migration status.
2. nDCG@5, Recall@10, and MRR.
3. Query latency.
4. Adapter availability.

```bash
make bench-head-to-head
```

The locked public headline comes from `benchmarks/head-to-head/baseline.json`. Real-data operator comparison remains a Phase J dogfood deliverable.

## Locked Reference Numbers (operator hardware, seed=42)

Benchmark G numbers are in the [Benchmark G section](#benchmark-g-compaction-recall-1) below.

These numbers are the published baselines for the v1.0 release line. CI regression guards lock the deltas relative to these. Hardware metadata is written next to every run. Reproduce on any machine with `make bench-all`.

### Benchmark A: Retrieval Quality

500-document synthetic corpus, 50 queries each anchored to one unique title-pair, evaluated with nDCG@5 / Recall@10 / MRR.

| Condition | nDCG@5 | Recall@10 | MRR |
|---|---|---|---|
| baseline_fts5_only | 0.801 | 1.00 | 0.734 |
| **pipeline_fts5_only** | **0.801** | **1.00** | **0.734** |
| pipeline_rrf_fts5_plus_bow, lexical ablation | 0.521 | 0.96 | 0.477 |

The production FTS5 path is the release metric. The BoW fusion ablation lowers nDCG@5 by 0.280 and Recall@10 by 0.04 on this fixture, so 3.6.0 does not present it as a retrieval improvement. These are the seed-42 figures committed in `benchmarks/retrieval/baseline.json`. `benchmarks/retrieval/regression_guard.py` fails CI on a production-path nDCG@5 drop greater than 0.02.

### Benchmark B: Latency

100 Stop-hook proxy invocations, 1000 retrieve queries on the indexed corpus, indexer scaling at 1k/3k/7k documents.

| Operation | p50 | p95 | p99 | Budget |
|---|---|---|---|---|
| Stop hook proxy | 1 ms | **2 ms** | 4 ms | 1000 ms (constraint C2) |
| Retrieve (FTS5 over 500 docs) | 2 ms | 3 ms | 6 ms | n/a |
| Indexer scaling, 7k docs | n/a | n/a | n/a | 25 s cold |

`benchmarks/latency/p95_guard.py` enforces Stop p95 under 1000 ms.

### Benchmark C: Adaptive Context Layer

Five representative shell outputs for `shell_compress`, 20-turn 5-doc simulated session for `injection_dedup`, seven budget points for `adaptive_topk`, full / keypoints / ref size comparison.

| Primitive | Effect |
|---|---|
| `shell_compress` | **88 percent** size reduction on redundant Bash logs |
| `injection_dedup` | **95 percent** skip rate in tight 20-turn sessions |
| `adaptive_topk` | smooth linear interpolation 5k -> top-10, 50k -> top-3 |
| `compressed_format=keypoints` | **46 percent** of full body tokens |
| `compressed_format=ref` | **12 percent** of full body tokens |

### Benchmark D: Migration Validation

Synthetic claude-mem fixture, TS CLI invoked via `npx tsx`, four invariants checked.

| Assertion | Result |
|---|---|
| `migrated_equals_seeded` | pass |
| `second_run_zero_new` | pass |
| `second_run_full_dedup` | pass |
| `redactions_match_seeded` | pass |

### Benchmark E: Head-to-Head Adapter

300-doc synthetic fixture with 30 queries run through `MnemeAdapter` and, when available, `ClaudeMemAdapter`. ClaudeMemAdapter is gated by `CLAUDE_MEM_BIN` environment variable or `claude-mem` on PATH. Baseline provenance lives in `benchmarks/head-to-head/baseline.json`.

| Adapter | Status | nDCG@5 | MRR |
|---|---|---|---|
| mneme | available | 0.831 | 0.772 |
| claude-mem | gated (not installed in CI) | n/a | n/a |

Real-data head-to-head against installed claude-mem is a Phase J dogfood week deliverable.

## Benchmark F: LongMemEval Schema Adapter

Validates the FTS5 retrieval adapter against deterministic fixtures that
conform to the pinned official LongMemEval schema. This is a schema and
plumbing regression test. It does not download the official dataset and does
not report a LongMemEval score.

```bash
make bench-longmemeval
```

## Benchmark G: Compaction Recall

Measures how much information the CCE self-heal recovers after a host
compaction drops a deterministic subset of session working-set items.

**What this measures, and what it does not.** This is a synthetic seeded
regression anchor (ADR-012 discipline). The compaction pattern (which items
survive), the token text length, and the rehydration budget are all under our
control. The numbers bound regression on this specific fixture; they are not a
claim about real-world compaction behavior or recall on arbitrary sessions.

**Method.** A seeded RNG builds K=50 synthetic `WorkingSetItem` facts and
wraps them in a `Checkpoint`. A second seeded RNG selects ~40 % of items to
survive compaction. A JSONL transcript is written containing only the surviving
items. Two conditions are measured:

- `baseline_no_selfheal`: recall = survivors / K (no self-heal, what the host
  left behind).
- `mneme_selfheal`: recall = (survivors ∪ rehydrated) / K, where `detect_dropped`
  finds the missing items and greedy rehydration fills them highest-salience first
  within the `rehydration_token_budget` (default 4000 tokens).

A negative probe confirms `recovered ⊆ original` — self-heal never invents a fact.

**Seed-42 result (locked baseline):**

| Condition | recall@K |
|---|---|
| baseline_no_selfheal | 0.40 |
| **mneme_selfheal** | **1.00** |
| headline_recall_gain | **+0.60** |

Self-heal achieves perfect recall on this fixture because the 30 dropped items
fit entirely within the 4000-token rehydration budget (each synthetic fact text
is ~50 chars / ~12 tokens). On real sessions with longer fact texts or a tighter
budget, partial recovery is expected and the regression guard allows up to a
0.05 absolute drop from this anchor.

```bash
make bench-compaction-recall
```

CI regression guard: `mneme_selfheal.recall_at_k` drop > 0.05 or
`headline_recall_gain` drop > 0.05 or `negative_probe.all_passed = false`
fails the build.

### Benchmark G: Compaction Recall

50-fact synthetic working set, 30 facts dropped by seeded compaction, 4000-token
rehydration budget, greedy highest-salience-first selection.

| Condition | recall@K |
|---|---|
| baseline_no_selfheal | 0.40 |
| **mneme_selfheal** | **1.00** |

`headline_recall_gain = +0.60`. `negative_probe.all_passed = true`
(0 invented facts). `benchmarks/compaction-recall/regression_guard.py` enforces
these anchors in CI.

## Historical Reference Numbers from Predecessor

For lineage, the internal reference implementation that mneme was extracted from achieved these numbers on a 7621-document corpus. They are not the v1.0 published baseline but documented here for traceability.

| Metric | Value | Conditions |
|---|---|---|
| FTS5 search p95 | 0.51 ms | 7621 documents, 105 queries |
| FTS5 build time (cold) | 25.59 s | same corpus |
| Failed queries | 0 of 105 | |
| Turkish casefold unit tests | 5 of 5 PASS | including KIYASLAMA edge case |
| Graphiti bi-temporal Cypher | 5 of 5 PASS | |
| RRF k=60 smoke tests | 4 of 4 PASS | |
