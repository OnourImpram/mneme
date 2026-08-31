# Upgrading mneme

This guide covers moving an existing installation between major lines.
Markdown is ground truth: no upgrade in mneme's history has ever
required touching your vault files, and every derived store (FTS5
index, claims table, graph) is rebuildable from them.

## 3.6.x to 4.1

Two breaking changes. Your markdown is untouched, as always — everything that
changes here is derived state or a response field.

### 1. The index must be rebuilt (schema 4)

Schema 4 makes the file path searchable, records a language per document, and
reads validity dates from frontmatter. None of that can be added to an existing
index in place, so 4.x refuses to read a schema-3 one rather than answering
from a half-understood index:

```
INDEX_STALE_OR_LOCALE_MISMATCH: FTS5 index schema '3' does not match the
schema this client speaks ('4').
```

Rebuild it:

```bash
mneme-core index rebuild --locale <tr|en>
```

**Pass `--locale` explicitly.** It defaults to `en`, and rebuilding a Turkish
vault under the English normalizer silently degrades Turkish matching rather
than failing — the index will look healthy and answer worse. If you are unsure
what the current index used, `mneme_health` reports it under `locale.profile`
before you rebuild.

Measured on a 12,317-document vault: 67 seconds, and the result was 33%
smaller than the schema-3 index it replaced.

### 2. `mneme_search` no longer returns `hits`

`hits` was deprecated in favour of `cards` and duplicated every result in the
response — the same data went over the wire twice on every query. It is gone
in 4.0.

If you consume the MCP response directly, read `cards`. Each entry carries the
same `path`, `title` and `snippet`, plus the evidence fields `hits` never had.
No migration is needed for normal Claude Code or MCP-client use; this only
affects code that parsed the raw tool response.

### Version constraints

The satellite packages now require the matching core: `mneme-graph`,
`mneme-code` and `mneme-cc-plugin` at 4.1.0 depend on `mneme-core>=4.1.0,<5`.
Upgrade them together — a mixed installation of 4.x satellites with a 3.x core
resolves to a schema-3 index under a schema-4 reader.

```bash
pipx upgrade mneme-cc-plugin
npm update -g mneme-mcp-server
mneme-core index rebuild --locale <tr|en>
```

### Not breaking, but worth knowing

4.1 changes how results are ranked: candidates are grouped by how many
distinct query terms appear in a document's title or path, ties break on how
canonical the path looks, and a Turkish/English term bridge lets an English
query reach a Turkish filename. Nothing about the API changes — the same query
returns better-ordered results. `CHANGELOG.md` records what was measured, and
what was measured and rejected.

## 3.5.x to 3.6.0

Upgrade the installed clients, then rebuild derived state once:

```bash
pipx upgrade mneme-cc-plugin
npm update -g mneme-mcp-server
mneme index rebuild
mneme doctor --verify-isolation
```

The rebuild reads Markdown ground truth and replaces only the derived FTS5
database under `.mneme/`. It does not rewrite vault Markdown. Mneme 3.6.0
refuses a concrete scope read when an older index does not contain scope
metadata. This replaces the 3.5 compatibility behavior that could widen a
concrete query while waiting for a rebuild. Exact `scope: "*"` remains an
intentional cross-scope read.

`mneme_checkpoint_list` and `mneme_working_set_load` now accept an optional
scope. Omitting it uses `MNEME_SCOPE`, then `default_scope` from
`~/.mneme/config.toml`, then `default`. Durable writes never accept `*`.

No semantic model is downloaded or added to the base package. The default
retrieval path remains local FTS5 BM25. Graphiti and provider-backed
compression remain explicit opt-in surfaces.

New 3.6.0 `--archive move` migrations bind the canonical source path into the
signed rollback manifest. A legacy signed schema v2 manifest that recorded only
a lexical alias, for example macOS `/var` instead of `/private/var`, has no
signed canonical restore target. Mneme therefore refuses automatic source
finalization and restoration for that legacy manifest. It preserves the signed
archive for manual, hash-verified recovery instead of guessing a destination.

## 2.x to 3.x

```bash
pipx upgrade mneme-cc-plugin        # pulls mneme-core 3.x as a dependency
npm update -g mneme-mcp-server      # MCP server for any MCP client
mneme doctor                        # verify the upgraded environment
```

### What changes at runtime

| Change | Effect on an upgraded 2.x setup |
|---|---|
| License | The 3.x line is Apache-2.0. Published 1.x/2.x artifacts remain MIT (see `NOTICE`). |
| Node floor | `mneme-mcp-server` declares `engines.node >=22`. Node 20 reached end-of-life in April 2026 and better-sqlite3 12.x ships no Node 20 Windows prebuild, so 3.x is tested on Node 22 and 24 only. |
| Session summary | The Stop-path session log now fills with a **deterministic, zero-LLM extractive summary by default**. No key, no network call, no latency on the hot path. |
| Temporal claims | The claim lifecycle (valid-from/to, supersedes, as-of, `temporal blame`) is built in on every profile. Tables are created inside `.mneme/` state on first `temporal index`; your markdown is not modified. |
| Autonomy | Off unless you opt in. An absent `policy.json` means zero autonomous edits, exactly like 2.x. Scaffold one with `mneme memory policy init`. |
| cc-plugin dependency | `mneme-cc-plugin` 3.x requires `mneme-core>=3.0.0,<4`; a clean install resolves this automatically. |

### Opting out of the default-on summary

Write `.mneme/summary.json` in your vault:

```json
{ "deterministic": false }
```

Set `"language": "tr"` in the same file for Turkish summary headings.
The opt-in LLM compression layer is unchanged from 2.x and stays off
by default.

### New opt-in surfaces (no action needed)

Team sync (`mneme sync push|pull`, redaction-before-share), the
loopback web console (`mneme-console --serve`), and the `mneme_propose`
MCP tool are all inert until configured. See `docs/COOKBOOK.md`
recipes 11 to 15.

### Within the 3.x line

- 3.0.1 restored the npm `mcpName` field and raised the Node engines
  floor; no user action.
- 3.1.0 pins the console Host header against DNS rebinding,
  trust-marks team-sync imports (`trust: external`,
  `payload_sha256`), and adds `memory policy init|validate`. Existing
  unmarked `team/` imports keep working; the next changed remote
  payload surfaces as a `.conflict` sidecar exactly as before.
- 3.5.0 introduced scope metadata and explicit cross-scope reads. 3.6.0 makes
  concrete reads fail closed until a legacy derived index is rebuilt, scopes
  CCE and temporal paths, and binds KG ingestion to deterministic Graphiti
  groups.

## 1.x to 3.x

Follow the 2.x steps; nothing else is required. The 1.x vault layout
is identical, and the FTS5 index is rebuilt automatically on first
use after upgrade.
