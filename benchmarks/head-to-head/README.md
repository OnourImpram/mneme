# Benchmark E - Head-to-head Retrieval

Runs identical query sets through every available memory adapter so
the v1.0 launch can publish honest, reproducible head-to-head numbers
against competing systems on the same data.

## Methodology

The harness uses the same synthetic corpus that Benchmark A consumes
(`mneme_core.bench.synth.build_synthetic_corpus`), but repackages it
into a claude-mem v13.2.0 SQLite database so every adapter can ingest
the same source. Each adapter implements three operations:

| Operation | Contract |
|---|---|
| `status() -> AdapterStatus` | Probe-only. Reports whether the adapter can run on this host. |
| `migrate(source_db, workdir)` | Ingest the claude-mem fixture into the adapter's storage. |
| `search(query, top_k=10)` | Return ranked `(doc_id, score)` hits. |

After migration, the harness issues every synthetic query through
each available adapter, scoring hits against the relevance set
(target document only). Adapters are responsible for translating
their internal doc identifiers to the canonical `cm-obs-{N}` shape
the relevance set uses.

## Adapters

### MnemeAdapter (production)

- Migrates via `npx tsx packages/mneme-mcp/src/cli/index.ts
  migrate-from-claude-mem`.
- Indexes via `mneme_core.fts5.indexer.index_vault`.
- Searches via `mneme_core.retrieval.rrf.retrieve` with the default
  `RetrievalConfig` (top-n 10, RRF k=60, FTS5-only since the
  benchmark runs without the dense backend).
- Available whenever `npx` is on PATH.

### ClaudeMemAdapter (stub)

- Reports `available=False` unless `CLAUDE_MEM_BIN` is set or the
  `claude-mem` binary is on PATH.
- Migrate is a no-op (claude-mem already owns the source DB).
- Search returns an empty list in the stub. Wiring is documented
  in-line: subprocess the `claude-mem search --json --top-k N`
  command and parse the result.

The stub exists so the benchmark surface is testable in CI without
gating on a competitor's install. Operators run the actual
head-to-head locally during Phase J dogfood week.

## Running

```bash
python benchmarks/head-to-head/run.py --output-format=json --output result.json
```

Flags:

- `--docs-per-topic 30` (default; 10 topics x 30 = 300 docs)
- `--queries-per-topic 3` (default; 30 queries total)
- `--hardware-output benchmarks/head-to-head/hardware.json`
- `--output result.json` (writes UTF-8 JSON without relying on shell redirect)

To wire claude-mem for a real comparison:

```bash
# Install claude-mem somewhere on PATH (or set CLAUDE_MEM_BIN).
# Then re-run the benchmark; ClaudeMemAdapter will become available.
python benchmarks/head-to-head/run.py --output-format=json --output result.json
```

## Reference run (operator hardware, MnemeAdapter only)

| Metric | Value |
|---|---|
| Fixture | 300 docs, 30 queries |
| Migrate elapsed | hardware-dependent |
| Avg query latency | hardware-dependent |
| nDCG@5 | 0.831 |
| Recall@10 | 1.000 |
| MRR | 0.772 |

These values are locked in `baseline.json` with the exact command,
fixture size, and reference hardware metadata. Numbers will move when
the packaged dense backend lands and when the corpus expands to
operator data during Phase J.

## Adding a third adapter

```python
class Mem0Adapter:
    def status(self) -> AdapterStatus: ...
    def migrate(self, source_db: Path, workdir: Path) -> dict: ...
    def search(self, query: str, top_k: int = 10) -> list[AdapterHit]: ...
```

Then add the new instance to the `adapters` list in `run.py`. No
harness change required.

## What this benchmark does not validate

- Real-world retrieval quality on operator data. The synthetic
  corpus is a fair head-to-head substrate but not a substitute for
  the Phase J dogfood metric.
- claude-mem v13.2.0 numbers; the stub by design produces no hits.
- Cost dimension (tokens consumed during a session). That is
  Benchmark C.
