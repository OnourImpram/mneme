# Client Integrations

mneme supports three integration tiers. Each tier is a strict superset of
the one below it in terms of automation, but the open adapter tier is the
easiest entry point for any MCP-capable client.

---

## Tier 1 — Claude Code (deepest native)

**Five lifecycle hooks + skills + MCP server.**

The Claude Code plugin (`mneme-cc-plugin`) registers five hooks in
`~/.claude/settings.json`:

| Hook | What it does |
|---|---|
| `SessionStart` | Primes vault context into the conversation preamble. |
| `PostToolUse` | Stages tool-use events for the session log after every Edit/Write/Bash/Task/MultiEdit. |
| `Stop` | Deterministic append to the daily session log — no LLM call, near-zero latency. |
| `PreCompact` | Saves working state before context compaction. |
| `SessionEnd` | Flushes any remaining staged events. |

Install:

```bash
mneme install --client claude-code --vault /path/to/vault
```

---

## Tier 2 — Codex and Antigravity (native-level)

**Hooks + skills + MCP server** (same coverage model as Claude Code, minus
`SessionEnd` for Antigravity which folds that into `Stop`).

```bash
# Codex
mneme install --client codex --vault /path/to/vault

# Google Antigravity / Gemini CLI
mneme install --client antigravity --vault /path/to/vault
```

---

## Tier 3 — Open MCP adapter (any MCP-capable client)

**MCP tools only. No auto-capture. No lifecycle hooks.**

This is an explicit opt-in surface designed for clients that speak the MCP
protocol but are not natively integrated with mneme: Kimi, Qwen, Cursor,
Cline, Claude Desktop in manual-tool mode, and any other client that reads
an `mcpServers` JSON object.

The model calls mneme's six MCP tools when it chooses to. Nothing fires
automatically.

### Option A — use the installer

```bash
mneme install \
  --client mcp \
  --config /path/to/your-client/mcp-config.json \
  --vault /path/to/vault
```

`--config` must point to the JSON file your client reads for MCP server
configuration. The installer merges exactly one key (`mcpServers.mneme`)
and leaves every other key and server entry untouched. The write is atomic
(tmp + os.replace) so a crash mid-write cannot truncate your config.

### Option B — add the stanza by hand

```json
{
  "mcpServers": {
    "mneme": {
      "command": "mneme-mcp",
      "env": {
        "MNEME_VAULT": "/path/to/vault"
      }
    }
  }
}
```

See `examples/mcp-config.json` for a complete minimal example.

### Uninstall

```bash
mneme uninstall \
  --client mcp \
  --config /path/to/your-client/mcp-config.json
```

Removes only the `mcpServers.mneme` entry; all other servers are preserved.

---

## MCP tools (all tiers)

All six tools are served by `mneme-mcp` regardless of which tier you use.

| Tool | Default behaviour | Gated behaviour |
|---|---|---|
| `mneme_search` | FTS5 BM25 search over the vault. | Dense retrieval (roadmap); KG enrichment when full-profile graph is active. |
| `mneme_recall` | Fetch full body of a note by path or session id. | — |
| `mneme_write` | Append a structured section to the vault. | — |
| `mneme_prime` | Build a token-budgeted session preamble from recent sessions and topic matches. | — |
| `mneme_summarize` | Summarize a topic across sessions (FTS5 default). | KG-enriched entity grouping when full-profile graph is active. |
| `mneme_timeline` | Temporally ordered events for a subject (FTS5 mtime sort). | Bi-temporal Graphiti facts when full-profile graph is active. |

"Gated" features require `--profile full` and a running local Neo4j instance.
The shipped default (`--profile lite`) uses FTS5 only.

---

## Honest capability summary

| | Claude Code | Codex | Antigravity | Open adapter |
|---|---|---|---|---|
| MCP tools | Yes | Yes | Yes | Yes |
| Auto-capture on file edits | Yes | Yes | Yes | No |
| Session-start context injection | Yes | Yes | Yes | No |
| Session-end log flush | Yes | Yes | Yes (via Stop) | No |
| Skills / slash commands | Yes | Yes | Yes | No |

The open adapter is intentionally minimal. If you want deeper automation,
install for a natively supported client.
