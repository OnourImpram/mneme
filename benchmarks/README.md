# Benchmarks

Reproducible measurement of mneme's performance claims. Each directory
ships a `run.py`, a per-benchmark `README.md`, and (where applicable) a
CI guard. See `docs/BENCHMARKS.md` for cross-benchmark methodology and
the locked baseline numbers used by the regression guards.

## Layout

- `retrieval/`: **Benchmark A** - synthetic 500-doc corpus with
  nDCG@5, Recall@10, MRR. Ships `regression_guard.py` and `baseline.json`.
- `latency/`: **Benchmark B** - Stop-hook proxy, retrieve, indexer
  p50/p95/p99 distributions. Ships `p95_guard.py` with a 1000ms
  default threshold matching `docs/CONSTRAINTS.md`.
- `cost/`: **Benchmark C** - Adaptive Context Layer effectiveness
  (`shell_compress` reduction, `injection_dedup` skip rate,
  `adaptive_topk` sweep, `compressed_format` size deltas).
- `migration/`: **Benchmark D** - end-to-end validation that
  `mneme-migrate` correctly converts a claude-mem v13.2.0 SQLite into
  the mneme vault. Invokes the TS CLI via `npx tsx`.
- `head-to-head/`: **Benchmark E** - adapter Protocol for comparing
  mneme to other memory systems on identical data. Ships MnemeAdapter
  (production) and ClaudeMemAdapter (stub).
- `longmemeval/`: **Benchmark F** - pinned official-schema adapter checks
  over deterministic synthetic fixtures. It is not an official dataset score.
- `compaction-recall/`: **Benchmark G** - deterministic CCE checkpoint loss
  detection and rehydration regression fixture.

## Running

All benchmarks at once:

```bash
make bench-all
```

Output goes to `benchmarks/_runs/` (gitignored). Each script also
writes a `hardware.json` capturing CPU, RAM, OS, Python version, and
Node version so the numbers can be interpreted in context.

Individual benchmarks:

```bash
make bench-retrieval
make bench-latency
make bench-cost
make bench-migration
make bench-head-to-head
make bench-longmemeval
make bench-compaction-recall
```

Or invoke the scripts directly with their flags:

```bash
python benchmarks/retrieval/run.py --output-format=json
python benchmarks/latency/run.py --sessions=100 --queries=1000
python benchmarks/cost/run.py --turns=20
python benchmarks/migration/run.py --observations=200
python benchmarks/head-to-head/run.py --docs-per-topic=30
python benchmarks/longmemeval/run.py
python benchmarks/compaction-recall/run.py --output-format=json
```

Use `--output path/to/result.json` on any runner to write UTF-8 JSON
without relying on shell redirect encoding.

## Reproducibility

- Pinned seed: `MNEME_BENCH_SEED=42` (Makefile defaults to this).
- Per-run `hardware.json` lists CPU model, core count, OS, Python and
  Node versions.
- All input data is synthetic and bytewise deterministic per seed.

## CI

`.github/workflows/bench.yml` runs every benchmark on Ubuntu with
artifact upload for manual dispatch and for PRs or pushes that touch
benchmarked packages, benchmark code, or the benchmark workflow.

## Adding a new benchmark

1. Create `benchmarks/<your-name>/` with `run.py` and `README.md`.
2. Add a Makefile target.
3. Add a job to `bench.yml`.
4. Document the methodology in `docs/BENCHMARKS.md`.
5. Open a pull request with the locked reference number.

## What benchmarks do and do not validate

| Benchmark | Validates | Does not validate |
|---|---|---|
| A | Synthetic retrieval quality, regression guard | Real-world vault quality |
| B | Stop-hook proxy + retrieve latency distribution | Production hook spawn overhead |
| C | Adaptive Context Layer per-primitive savings | Cumulative interaction in real sessions |
| D | Migration tool structural correctness | Migration of arbitrary user data |
| E | Adapter contract + head-to-head harness shape | Numbers vs actual claude-mem (Phase J) |
| F | LongMemEval official-schema adapter plumbing | A score on the official dataset |
| G | Synthetic CCE compaction-recall recovery | Real-session compaction quality |

Real-world numbers vs operator vault and live claude-mem comparison
are Phase J dogfood week deliverables.
