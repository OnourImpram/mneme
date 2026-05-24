# Vault Contract

The vault is a user-owned directory of markdown files. mneme reads, writes, and indexes the vault, but never owns it. You can move it, back it up, rename files, and edit them directly with any editor.

## Layout

```
vault/
├── sessions/                  conversation-level records
│   └── YYYY-MM-DD/
│       └── HH-MM-SS-<slug>.md
├── topics/                    long-lived topic notes
│   └── <topic-slug>.md
├── references/                derived reference material
│   └── <slug>.md
├── patterns/                  Signal/Action/Outcome heuristics
│   └── <pattern-slug>.md
├── trajectories/              per-session decision trails
│   └── YYYY-MM-DD/
│       └── <trajectory-id>.md
├── imported/                  migration imports (provenance-marked)
│   └── claude-mem/
│       ├── YYYY-MM-DD/        per-day observation imports
│       ├── _sessions/         session summary imports
│       ├── _prompts/          user prompt imports
│       ├── _archive/          source DB snapshot (if --archive copy|move)
│       └── _manifest.json     per-migration audit summary
├── inbox/                     unprocessed captures
└── .mneme/                    derived indexes (gitignored if you wish)
    ├── fts5.sqlite
    ├── leann.index
    ├── graphiti/
    ├── kg_episode_queue.jsonl
    ├── kg_cost_ledger.jsonl   tracks compression and KG-build LLM spend
    ├── injection_dedup/       per-session hash sets for distill
    └── audit/                 privacy redaction SHA256 trail
        └── YYYY-MM-DD.jsonl   one record per redaction event
```

## Frontmatter Specification

Every vault file begins with YAML frontmatter. mneme recognizes nine `type` values, each with a stable schema.

```yaml
---
id: 2026-05-19T10-30-00-mneme-architecture-decision
type: session
created: 2026-05-19T10:30:00Z
modified: 2026-05-19T10:45:12Z
tags: [architecture, retrieval, rrf]
session_id: 01HZX...
source: claude-code
schema_version: 1
---
```

Required fields across all types: `id`, `type`, `created`. `schema_version` is recommended but defaults to `1` when omitted, so the parser does not refuse documents that lack it. All other fields are optional unless noted below.

### Recognized Types

| `type` | Directory | Required extras | Purpose |
|---|---|---|---|
| `session` | `sessions/` | `session_id`, `source` | One Claude Code session record. |
| `topic` | `topics/` | none | Long-lived note about a recurring subject. |
| `reference` | `references/` | none | Curated reference material extracted from sessions. |
| `pattern` | `patterns/` | `signal`, `action`, `outcome` body sections | Reusable Signal/Action/Outcome heuristic. |
| `trajectory` | `trajectories/` | `session_id`, append-only `## Step N` body | Per-session decision trail. |
| `compressed` | `sessions/YYYY-MM-DD.md` (appended inline under `## Auto-captured observations` marker) | `source_session_id`, `compression_score`, `content_hash` extras | Background-AI-compressed observation block, opt-in only. |
| `observation` | `imported/claude-mem/YYYY-MM-DD/` | `source`, `content_hash`, optional `redacted_count` extras | claude-mem observation import. |
| `session_summary` | `imported/claude-mem/_sessions/` | `source`, `content_hash` extras | claude-mem session summary import. |
| `user_prompt` | `imported/claude-mem/_prompts/` | `source`, `content_hash` extras | claude-mem user prompt import. |

### Migration Provenance Extras

Files written by `mneme-migrate` carry provenance fields beyond the canonical schema so the import path is fully auditable.

```yaml
---
id: cm-obs-1453
type: observation
created: 2026-04-21T14:22:11Z
schema_version: 1
source: claude-mem-v13.2.0
content_hash: 9f3c2d...
original_type: code-mode
project: my-project
original_model: claude-opus-4-7
agent_type: main
redacted_count: 2
---
```

## Atomic Write Pattern

mneme writes to a temporary file in the same directory, then renames to the final path. This is atomic on POSIX and Windows NTFS. Partial writes are impossible.

```
write to vault/sessions/2026-05-19/10-30-00-foo.md.tmp
fsync the temp file
rename to vault/sessions/2026-05-19/10-30-00-foo.md
fsync the directory (POSIX only)
```

The same primitive (`mneme_core.vault.atomic_write.atomic_write_text`) is shared between hooks, the indexer, the migration tool, and the trajectory recorder. Path traversal is rejected by `assertWithinVault` before the temp file is created.

## Indexing Trigger

mneme detects vault changes via filesystem watch (production) or a periodic scan (low-power mode). Changed files are re-indexed within 5 seconds.

## Configuration

`VaultConfig` resolves vault location via this order:

1. `MNEME_VAULT` environment variable.
2. `--vault` CLI flag.
3. `~/.mneme/config.toml` `vault` key.
4. Walk parent directories looking for a `.mneme/` marker, similar to `.git`.
5. Default: `~/mneme-vault/`.

## Backup and Migration

```bash
# Backup
tar czf mneme-vault-$(date +%F).tar.gz vault/

# Migrate to new machine
tar xzf mneme-vault-*.tar.gz
export MNEME_VAULT=$PWD/vault
mneme rebuild-indexes
```

Rebuild is idempotent and produces equivalent retrieval results.

## In-Place Profile Upgrade

The vault is profile-agnostic. Upgrading from lite to standard or full only rebuilds derived indexes under `.mneme/`. No vault file is rewritten, no frontmatter is migrated.

```bash
mneme install --upgrade-profile=standard
```

The upgrade path is:

1. Detect current profile from `.mneme/profile.json`.
2. Install the additional Python dependencies for the target tier.
3. Build the new index types (LEANN dense for standard, Graphiti episodes for full).
4. Update `.mneme/profile.json` and verify with `mneme doctor`.

Downgrade is also non-destructive: indexes for the removed tier stay on disk but are unused. Run `mneme reset --prune-indexes` if you want them removed.
