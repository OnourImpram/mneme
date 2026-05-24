# Benchmark B - Latency

Measures three latency-critical operations in the mneme pipeline:
the Stop-hook critical-path write, end-to-end retrieval, and full
vault indexer throughput.

## Methodology

### Stop-hook proxy

`atomic_write_text` is the same primitive the plugin's Stop hook uses
to append session records, so timing it directly is a representative
proxy. The benchmark writes 100 distinct session markdown files (each
about 1 KB) and reports the latency distribution.

### Retrieve

A 300-doc synthetic corpus is materialized once and indexed once. The
benchmark then issues 1000 `retrieve(query, config)` calls cycling
through the synthetic query set. Each call opens, reads, and closes
the SQLite database, which matches the production access pattern
(the MCP server does not hold a persistent connection across calls).

### Indexer scaling

`index_vault` is run cold at three corpus sizes (1k, 3k, 7k docs) with
a fresh database per size to expose how throughput scales with corpus
size. Wall time and derived docs/second are reported.

## Running

```bash
python benchmarks/latency/run.py --output-format=json > result.json
```

Optional flags:

- `--sessions 100` (default)
- `--queries 1000` (default)
- `--skip-indexer-scaling` to drop the 1k/3k/7k sweep
- `--hardware-output benchmarks/latency/hardware.json`

## CI guard

```bash
python benchmarks/latency/p95_guard.py result.json --threshold-ms=1000
```

Exits non-zero when `stop_hook_proxy_ms.p95` exceeds the threshold.
Default 1000ms matches `docs/CONSTRAINTS.md` (Zero-LLM-Stop).

## Reference numbers (operator hardware, single run)

| Operation | p50 | p95 | p99 |
|---|---|---|---|
| Stop-hook proxy | ~1.4 ms | ~2.1 ms | ~2.3 ms |
| Retrieve (one-shot, 300-doc index) | ~2.0 ms | ~2.9 ms | ~3.1 ms |

Indexer scaling (cold):

| Corpus | Docs/sec |
|---|---|
| 1k | ~3500 |
| 3k | ~1500 |
| 7k | ~3200 |

The 3k throughput dip is reproducible; SQLite WAL checkpoints near
that size cost a fixed amount of wall time that dominates a small
corpus. The 7k number recovers because amortization wins. Numbers
above are illustrative; CI captures hardware.json alongside each run.

## What this benchmark does not validate

- Production hook latency under Claude Code's real invocation cost.
  The proxy isolates `atomic_write_text` from process-spawn time.
- Cold-start retrieval. The benchmark indexes once and queries warm.
  Cold-start adds the index-open cost, which is ~1 ms on most setups.
- Cross-OS variance. CI matrix (ci.yml) covers Ubuntu / macOS /
  Windows separately; reference numbers above are operator hardware.
