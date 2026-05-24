# Benchmark D - Migration Validation

End-to-end validation that `mneme-migrate migrate-from-claude-mem`
correctly converts a claude-mem v13.2.0 SQLite database into a mneme
vault.

## Methodology

The benchmark materializes a synthetic claude-mem schema with N
observations (default 200), seeds every fifth observation with a
`<private>...</private>` block, and adds one session summary plus
one user prompt. It then invokes the TypeScript migration CLI via
`npx tsx` against the fixture and checks four structural assertions:

1. **`migrated_equals_seeded`**: every input row becomes one markdown
   file (observation count parity).
2. **`second_run_zero_new`**: a second invocation against the same
   fixture migrates zero new rows.
3. **`second_run_full_dedup`**: a second invocation reports every
   row as skipped via the content-hash dedup path.
4. **`redactions_match_seeded`**: the count of redactions applied is
   at least as high as the count of seeded `<private>` blocks. Higher
   is acceptable because session summaries and user prompts have
   string columns the migrator also scans.

The benchmark stops short of evaluating retrieval quality on migrated
content - that is the Phase J operator dogfood objective and lives in
`benchmarks/head-to-head/`.

## Prerequisites

```bash
pnpm install --frozen-lockfile
```

so `npx` finds the workspace's local `tsx`. The benchmark is a no-op
(`status: skipped`) when `npx` is not on PATH.

## Running

```bash
python benchmarks/migration/run.py --observations 200 --output-format=json > result.json
```

Flags:

- `--observations 200`
- `--hardware-output benchmarks/migration/hardware.json`

## Reference output (operator hardware, observations=50)

```json
{
  "first_run": {
    "rc": 0,
    "elapsed_seconds": 5.5,
    "observations_migrated": 50,
    "redactions_applied": 10
  },
  "second_run": {
    "rc": 0,
    "elapsed_seconds": 5.9,
    "observations_migrated": 0,
    "observations_skipped_dedup": 50
  },
  "assertions": {
    "migrated_equals_seeded": true,
    "second_run_zero_new": true,
    "second_run_full_dedup": true,
    "redactions_match_seeded": true
  }
}
```

The 5-6 second elapsed time includes `tsx` JIT compilation of the
TypeScript CLI; once compiled to dist the cold-start drops to roughly
0.5s. CI pipelines can optionally `pnpm --filter mneme-mcp build`
before this benchmark to use the compiled CLI instead.

## What this benchmark does not validate

- Migration fidelity for real claude-mem data (Phase J dogfood
  measures retrieval-quality parity on the 1700+ observation
  operator vault).
- Migration of arbitrary user data such as personal text that the
  fixture cannot represent.
- Performance at full-vault scale (the synthetic 200-observation
  fixture is structural, not large-scale).
