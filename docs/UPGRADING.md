# Upgrading mneme

This guide covers moving an existing installation between major lines.
Markdown is ground truth: no upgrade in mneme's history has ever
required touching your vault files, and every derived store (FTS5
index, claims table, graph) is rebuildable from them.

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

## 1.x to 3.x

Follow the 2.x steps; nothing else is required. The 1.x vault layout
is identical, and the FTS5 index is rebuilt automatically on first
use after upgrade.
