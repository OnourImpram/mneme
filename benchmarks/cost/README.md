# Benchmark C - Cost (Adaptive Context Layer)

Quantifies token-saving from the four Adaptive Context Layer
primitives. The token count is approximated as
`ceil(len(text) / 4)` (industry-standard BPE heuristic, ~10% accuracy
on prose). For exact numbers swap in a real tokenizer.

## Methodology

### shell_compress

Five representative bash outputs (directory listing, Python stack
trace, repeated log line, pip install output, ANSI-colored test run)
fed through `compress_shell_output`. Reduction percentage is reported
per-entry and overall.

### injection_dedup

Simulated 20-turn session with 5 unique docs cycled every turn. The
first encounter of each doc records `mark_injected`; the remaining 95
encounters short-circuit via `has_injected`. Skip rate is the
fraction of encounters that avoid re-injection.

### adaptive_topk

Walks through seven context-usage points (0, 1k, 5k, 10k, 25k, 50k,
100k tokens used) and records the top-k value `adaptive_topk` returns
at each. Confirms the linear-interpolation policy clamps correctly.

### compressed_format

Renders one representative pattern document at all three injection
levels (`full`, `keypoints`, `ref`) and reports byte and token sizes
plus the saving versus the `full` baseline.

## Running

```bash
python benchmarks/cost/run.py --output-format=json --output result.json
```

Flags:

- `--turns 20`
- `--unique-docs 5`
- `--hardware-output benchmarks/cost/hardware.json`
- `--output result.json`

## Reference numbers (operator hardware, single run)

### shell_compress

| Entry | Original | Compressed | Ratio |
|---|---|---|---|
| `ls -la` listing (40 entries) | ~3 KB | ~0.2 KB | ~6% |
| Python stack trace (15 frames) | ~1 KB | ~0.4 KB | ~40% |
| Repeated log line (12 copies) | 143 B | 17 B | 12% |
| `pip install` output | 363 B | 362 B | ~100% (already minimal) |
| ANSI-colored test output | 77 B | 49 B | 64% |

The pip output ratio of 100% is expected: structured installer
output has no adjacent duplication, no listing-shaped runs, and no
ANSI escapes. Compression is a lossless no-op there. The directory
listing and repeated log line show the strongest reductions; both
are common in real Claude Code sessions.

### injection_dedup

| Metric | Value |
|---|---|
| Encounters | 100 |
| Skipped | 95 |
| Skip rate | **95%** |

The 20-turn / 5-doc shape produces a high skip rate because the same
5 docs are touched every turn. Real sessions with more doc churn
show lower skip rates; this is the upper bound for a tight session.

### adaptive_topk

| Context tokens used | top_k |
|---|---|
| 0 | 10 |
| 1k | 10 |
| 5k | 10 |
| 10k | 9 |
| 25k | 7 |
| 50k | 3 |
| 100k | 3 |

Defaults clamp at 10 below 5k and at 3 above 50k, with linear
interpolation between. Operators tune via `TopKPolicy`.

### compressed_format

| Level | Bytes | Tokens (approx) | Saving vs full |
|---|---|---|---|
| `full` | 408 | 102 | - |
| `keypoints` | 220 | 55 | **46%** |
| `ref` | 47 | 12 | **88%** |

`keypoints` is the practical re-injection format; `ref` is the
emergency-budget format used when the doc has already appeared
and context pressure is high.

## What this benchmark does not validate

- Real session token consumption. Phase J dogfood week measures
  end-to-end session cost on operator data.
- Tokenizer-exact numbers. The `/4` heuristic biases prose-heavy
  text slightly low and code-heavy text slightly high.
- Cumulative interaction between the four primitives. Bench numbers
  are isolated; the production hook layer composes them.
