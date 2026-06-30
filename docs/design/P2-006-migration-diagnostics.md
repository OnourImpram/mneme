# P2-006 — Migration Diagnostics: Dry-Run Diff, Per-File Decision Log, Rollback

## Capability

Give operators visibility into what `mneme migrate` (claude-mem import) and
`mneme index rebuild` will do before they commit: a dry-run diff of affected
files, a per-file JSON decision log, and a rollback path that restores the
pre-migration snapshot.

## Current State

`packages/mneme-core/src/mneme_core/cli.py`:

- `mneme index rebuild` (line 894 warning: `"run: mneme index rebuild to migrate"`)
  rebuilds the FTS5 database in-place.  No dry-run flag, no per-file log,
  no rollback.
- The migration doc is `docs/MIGRATION-FROM-CLAUDE-MEM.md`.
- No dedicated `mneme migrate` subcommand exists; migration is a manual
  multi-step guide followed by `mneme index rebuild`.

There is no snapshot mechanism; once rebuild runs, the old index is gone.

## Proposed Design

### `mneme migrate --dry-run`

Add a `migrate` Click command group with a `--dry-run` flag.

```
mneme migrate [--dry-run] [--log FILE] [--source claude-mem]
```

Dry-run mode:
1. Discovers source files (claude-mem session JSONL, memory markdown) using
   the same path resolution as the live migration.
2. For each file, computes what action would be taken:
   `{"file": "...", "action": "import|skip|overwrite", "reason": "..."}`.
3. Writes the per-file decision log to `--log FILE` (default `stderr` as
   pretty-printed JSON lines).
4. Prints a summary diff: `N files would be imported, M skipped, K overwritten`.
5. Exits 0 with no writes when `--dry-run` is set.

### Per-file decision log

Format: JSONL, one record per source file:

```json
{"ts": "ISO", "file": "relative/path.md", "action": "import",
 "reason": "not present in vault", "size_bytes": 1234}
```

The log is written atomically to the path given by `--log`; when omitted,
a default path under `{vault}/.mneme/migrate-log-{date}.jsonl` is used.

### Rollback

Before any write, `migrate` writes a rollback manifest:
`{vault}/.mneme/migrate-rollback-{ts}.json` listing every file that will be
created or overwritten together with its pre-migration content hash.

`mneme migrate --rollback {manifest}` reads the manifest and either deletes
newly-created files or restores overwritten files from a companion
`.mneme/migrate-backup/` directory created at migration time.

### Extension Point

`cli.py` — new `@cli.group()` for `migrate` alongside existing `index`,
`temporal`, `cce` groups.  The scan and decision logic lives in a new
`mneme_core/migration/` subpackage so it can be unit-tested without the
Click layer.  The FTS5 rebuild step (`mneme index rebuild`) is unchanged;
`migrate` is a pre-step that populates vault markdown before indexing.

## Feature-Flag / Rollout Plan

`--dry-run` flag on the new `migrate` command — explicit opt-in per
invocation.  No default-behavior change to `mneme index rebuild`.  The
rollback manifest is written unconditionally on a live `migrate` run;
`--no-backup` flag can skip it for performance on very large vaults.
