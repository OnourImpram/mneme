# mneme-cc-plugin

Claude Code plugin manifest for [mneme](https://github.com/TheGoatPsy/mneme).

## Hooks

| Hook | Purpose | Latency budget |
|---|---|---|
| `PostToolUse` | Capture tool output with `distill.shell_compress` compression and stage for indexing. | non-blocking background |
| `SessionStart` | Inject preflight vault context with `distill.injection_dedup`. | under 500ms p95 |
| `Stop` | Append session summary deterministically. No LLM call on the critical path. | under 1s p95 |
| `PreCompact` | Snapshot pre-compaction state for recovery. | under 200ms p95 |
| `SessionEnd` | Flush staging buffers, schedule opt-in background compression. | under 500ms p95 |

## Commands

- `/mneme:prime` loads context for the current task.
- `/mneme:recall` retrieves a specific session.
- `/mneme:migrate` runs one-command migration from claude-mem.

## Skills

- `mneme-prime` is the context priming workflow.
- `mneme-search` is the vault search workflow with hybrid retrieval.

## Installation

```bash
mneme install --profile=lite
```

This mutates your Claude Code `settings.json` in a BOM-safe way (Windows-friendly), invokes Python via the launcher (`py -3` on Windows), and registers all hooks, commands, the MCP server, and skills.

## Three-Tier Install Profiles

- `lite`: hooks + commands + 6 MCP tools (Python + Node only).
- `standard`: lite + LEANN dense embeddings + RRF fusion + `mneme_summarize`.
- `full`: standard + Graphiti temporal knowledge graph + `mneme_timeline` (Docker + Neo4j required).

Upgrade in place: `mneme upgrade --profile=standard`.

## License

MIT. See LICENSE in the repository root.
