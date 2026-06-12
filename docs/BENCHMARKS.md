# Benchmarks

mneme publishes five reproducible benchmarks. Run them with `make bench-all` or individually as documented below.

## Reproducibility Contract

All benchmarks consume a deterministic synthetic corpus built from a fixed seed. Two runs with the same seed produce byte-identical input data.

```bash
export MNEME_BENCH_SEED=42   # default, set in Makefile
make bench-all               # writes to benchmarks/_runs/ (gitignored)
```

Each run also writes a `hardware.json` capturing CPU model, core count, RAM, OS, Python version, and Node version. This lets results be interpreted in context across operator hardware, CI runners, and contributor machines.

**Re-verified for the 3.0 line (2026-06-12, seed 42, operator hardware):** after the 3.0 feature work (deterministic session distillation, temporal de-gate, policy autonomy, team sync, web console) the locked anchors hold unchanged — Benchmark A nDCG@5 = 0.893, Recall@10 = 1.0; Benchmark B Stop-hook proxy p95 = 2.0 ms, retrieve p95 = 9.8 ms. The new 3.0 surfaces stay off the Stop hot path by design, and the numbers confirm it.

## Methodology

All benchmarks pin hardware specs in a `hardware.json` file alongside results. CI uses GitHub Actions ubuntu-latest runners (2 vCPU, 7 GB RAM). Developer machines may show different absolute numbers but consistent relative deltas. CI regression guards lock the deltas, not the absolutes.

## Benchmark A: Retrieval Quality

Synthetic 500-document corpus with 50 title-anchored queries and hard negatives. Metrics: nDCG@5, Recall@10, MRR.

Conditions compared:

- `pipeline_rrf_fts5_plus_bow`, shipped RRF code path with FTS5 plus a deterministic BoW surrogate.
- `pipeline_fts5_only`, production wrapper with the FTS5 leg only.
- `baseline_fts5_only`, direct BM25 baseline.

The BoW leg is a benchmark surrogate for fusion regression testing. It is not a shipped LEANN dense adapter.

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

These numbers are the published baselines for the v1.0 release line. CI regression guards lock the deltas relative to these. Hardware metadata is written next to every run. Reproduce on any machine with `make bench-all`.

### Benchmark A: Retrieval Quality

500-document synthetic corpus, 50 queries each anchored to one unique title-pair, evaluated with nDCG@5 / Recall@10 / MRR.

| Condition | nDCG@5 | Recall@10 | MRR |
|---|---|---|---|
| baseline_fts5_only | 0.801 | 0.92 | 0.776 |
| pipeline_fts5_only | 0.801 | 0.92 | 0.776 |
| **pipeline_rrf_fts5_plus_bow** | **0.893** | 0.96 | 0.872 |

Delta: RRF fusion gains **+9.2 nDCG@5 points** over FTS5 alone. `benchmarks/retrieval/regression_guard.py` fails CI on any drop greater than 0.02.

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
