# mneme for Antigravity

mneme adds vault-native memory to Antigravity via ten MCP tools served
by `mneme-mcp`. Markdown files are the ground truth; the MCP server
is a read/write interface over that vault.

## MCP tools

| Tool | When to call |
|---|---|
| `mneme_prime` | Session start or before continuing prior work — retrieves a token-budgeted preamble of relevant vault context. |
| `mneme_search` | User asks a recall question — FTS5 BM25 search over the vault. |
| `mneme_recall` | Pull the full body of a specific vault note by path. |
| `mneme_write` | Persist a new note or append to an existing one. |
| `mneme_summarize` | Summarize a vault note or a set of search hits into a compact digest. |
| `mneme_timeline` | Retrieve temporally ordered events from the knowledge graph. |

## Lifecycle hooks (automatic)

Hooks fire without any agent action required:

- **SessionStart** — runs `mneme hook session-start`; primes context from the vault.
- **PostToolUse** — runs `mneme hook post-tool-use` after Edit/Write/Bash/Task/MultiEdit; stages events for the session log.
- **Stop** — runs `mneme hook stop`; deterministic append to the daily session log, no LLM call.
- **PreCompact** — runs `mneme hook pre-compact`; saves working state before context compaction.

Antigravity has no dedicated SessionEnd event. The Stop hook absorbs
session-end flushing, matching the Codex plugin's coverage model.

## Ground truth rule

Never invent vault content. If `mneme_search` returns no hits, say so.
Do not hallucinate note paths, titles, or prior decisions.
