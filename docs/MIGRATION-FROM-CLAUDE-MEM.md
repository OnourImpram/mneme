# Migration from claude-mem

A one-command migration tool that converts your claude-mem v13.2.0 SQLite database into a mneme vault. Distributed as the `mneme-migrate` bin in the `mneme-mcp` npm package.

## Quick Start

```bash
mneme-migrate migrate-from-claude-mem \
  --source ~/.claude-mem/db.sqlite \
  --vault ~/mneme-vault \
  --archive copy
```

This:

1. Opens the claude-mem SQLite database in read-only mode.
2. Reads observations, session summaries, and user prompts.
3. Applies privacy redaction (`<private>...</private>` segments replaced with `<REDACTED>`).
4. Computes SHA256 content hash per record for idempotency.
5. Writes one markdown file per record with canonical mneme frontmatter and claude-mem provenance extras.
6. Snapshots the source SQLite into `vault/imported/claude-mem/_archive/` (when `--archive copy`).
7. Writes a `_manifest.json` per-run audit summary.
8. Writes a hash-checked rollback manifest at `vault/.mneme/migrations/<run-id>/rollback.json`.

## Output Layout

```
vault/imported/claude-mem/
├── 2026-04-21/
│   └── cm-obs-1453.md           one file per observation
├── _sessions/
│   └── cm-sess-312.md           one file per session summary
├── _prompts/
│   └── cm-prompt-4180.md        one file per user prompt
├── _archive/                    only when --archive copy|move
│   └── claude-mem.db
└── _manifest.json               per-run audit summary
```

The migration result includes the rollback manifest path as `rollback_manifest` in
`imported/claude-mem/_manifest.json`. The rollback manifest is kept under
`.mneme/migrations/` so it remains separate from imported memory content.

## Idempotent Re-Run

The migration tool is idempotent. Re-running on the same source produces zero new files. Idempotency comes from three layers:

1. **Stable filenames**: `cm-obs-{id}.md`, `cm-sess-{id}.md`, `cm-prompt-{id}.md` deterministically derived from the claude-mem primary key.
2. **Content hash**: SHA256 over a canonical projection of the record. Stored in frontmatter `content_hash`.
3. **Tiny regex extractor**: reads the existing file's `content_hash` line without a YAML parser. If the hash matches, the write is skipped.

```bash
# Second run after edits in claude-mem
mneme-migrate migrate-from-claude-mem --source ~/.claude-mem/db.sqlite --vault ~/mneme-vault
# [mneme-migrate] 12 new, 1742 dedup-skipped (content_hash match)
```

## Side-by-Side Validation

Before committing fully to mneme, run both systems against the same vault and compare:

```bash
mneme-migrate migrate-from-claude-mem --source ~/.claude-mem/db.sqlite --vault ~/test-vault
make bench-migration  # runs benchmark D from the launch plan
```

Benchmark D reports structural correctness against a synthetic fixture. Target is all four assertions passing.

## Frontmatter Mapping

| claude-mem column | mneme frontmatter key |
|---|---|
| `id` | `legacy_id` |
| `created_at` | `created` |
| `content` | (body) |
| `session_id` | `session_id` |
| `mode` (if present) | `tags[]` (stripped of any locale suffix) |
| `entities` (if present) | `tags[]` |

## What is Lost

- Compression metadata (claude-mem stores compression scores; mneme generates its own on first compression run).
- LLM-generated summaries (mneme stores raw observations and lets you opt into compression separately).
- Cross-session embeddings (mneme rebuilds these from scratch).

## What is Preserved

- All raw observations and timestamps.
- Session boundaries.
- Tags and entity associations.
- Original content verbatim.

## The --archive Tri-State

The archive flag accepts three values, defaulting to the safest.

| Value | Behavior | Source SQLite after run |
|---|---|---|
| `preserve` (default) | No archive copy. Source untouched. | unchanged at original path |
| `copy` | Snapshot source into `vault/imported/claude-mem/_archive/claude-mem.db`. | unchanged at original path, snapshot also in vault |
| `move` | Snapshot, then delete the source. **Requires** `--confirm-delete`. | deleted from original path, only the snapshot exists |

The `move` mode is gated by a two-factor confirmation.

```bash
# Refuses to run without --confirm-delete
mneme-migrate migrate-from-claude-mem --archive move ...
# Error: --archive move requires --confirm-delete (two-factor gate)

# Explicit delete after migration
mneme-migrate migrate-from-claude-mem --archive move --confirm-delete ...
```

This design avoids accidental destruction while leaving a clean cutover path for power users.

## Privacy Redaction

Every text field is scanned for `<private>...</private>` segments before writing. Matched content is replaced with `<REDACTED>`. The number of redactions per record is surfaced in frontmatter as `redacted_count`, and aggregate redaction counts are recorded in the migration manifest.

```
[mneme-migrate] redacted 27 <private> blocks across 1754 records
```

This honors Constraint C4: private content cannot reach the vault, the index, or the knowledge graph.

## The _manifest.json Audit Summary

Each run writes a manifest capturing what was migrated.

```json
{
  "ran_at": "2026-05-19T16:42:11Z",
  "source": "/home/u/.claude-mem/db.sqlite",
  "vault": "/home/u/mneme-vault",
  "archive_mode": "copy",
  "stats": {
    "observations": {"new": 12, "dedup_skipped": 1742, "redacted": 0},
    "session_summaries": {"new": 4, "dedup_skipped": 308},
    "user_prompts": {"new": 22, "dedup_skipped": 4158, "redacted": 3}
  },
  "duration_ms": 4172
}
```

Future runs append new manifests rather than overwrite, so the history is preserved.

## Windows Considerations

On Windows, the migration tool invokes `npx tsx` for TypeScript execution. The Windows cmd-shim quirk (`npx` resolves to `npx.cmd` and cannot be invoked through `subprocess.run` without a shell) is handled by resolving the absolute path with `shutil.which("npx")` before spawning.

You should not need to do anything special on Windows. The Bench D head-to-head also uses this same resolution under the hood.

## Verification

After migration, run Benchmark D to verify structural correctness.

```bash
make bench-migration
```

The benchmark builds a synthetic claude-mem fixture, runs `mneme-migrate` over it, and asserts four invariants:

1. `migrated_equals_seeded`: every seeded observation appears in the vault.
2. `second_run_zero_new`: re-running produces no new files.
3. `second_run_full_dedup`: every record on the second run is dedup-skipped.
4. `redactions_match_seeded`: the redaction count equals the seeded `<private>` count.

All four pass on the seeded fixture. Real-data parity against an operator vault is a Phase J dogfood week deliverable.

## Rollback

Every non-dry migration creates a rollback manifest at:

```text
<vault>/.mneme/migrations/<run-id>/rollback.json
```

The normal rollback command is:

```bash
mneme-migrate rollback \
  --vault ~/mneme-vault \
  --manifest ~/mneme-vault/.mneme/migrations/<run-id>/rollback.json
```

Before restoring anything, mneme compares the current imported tree with the exact
post-migration file hashes recorded in the manifest. If any migrated file was added,
removed, or changed after the migration, rollback is refused and the vault is left
untouched. The same exact-hash check applies to the archived source database before
it can be restored.

For `--archive preserve` and `--archive copy`, the command above restores the
imported tree. For `--archive move`, source restoration is a separate destructive
boundary and requires an explicit second confirmation:

```bash
mneme-migrate rollback \
  --vault ~/mneme-vault \
  --manifest ~/mneme-vault/.mneme/migrations/<run-id>/rollback.json \
  --source ~/.claude-mem/claude-mem.db \
  --confirm-source-restore
```

The migration itself still requires `--confirm-delete` when `--archive move` is
selected. The `--source` value must resolve to the original path bound into the
signed rollback manifest. `--confirm-delete` authorizes removal during migration.
It does not authorize source restoration during rollback.

Rollback is idempotent. Repeating a successful rollback returns an already rolled back
result without changing the vault again. Manifests and snapshot paths must stay
inside the selected vault. Unsafe paths, missing snapshot evidence, symlinked or
unstable source parents, and hash mismatches fail closed. A prepared or incomplete
migration is not treated as rollbackable.
