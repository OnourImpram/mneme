# Using mneme with Codex

mneme is Claude-Code-native by origin. Its retrieval core (`mneme-core`),
its MCP server (`mneme-mcp`), and its vault markdown contract are
client-neutral, so mneme also runs inside the OpenAI Codex CLI as an
additive layer with no loss of fidelity. Codex support sits on top of the
same core that powers the Claude Code experience. It does not replace it
and does not genericize it.

## Two ways to add mneme to Codex

### 1. The Codex plugin (recommended)

Brings skills, the MCP server, and lifecycle hooks together.

```bash
codex plugin marketplace add OnourImpram/mneme
# then enable the mneme plugin and trust its hooks when prompted
```

Codex treats plugin-bundled hooks as non-managed, so you review and trust
the hook definitions on first run. The plugin lives at
`packages/mneme-codex-plugin`.

### 2. The installer (MCP server only)

Wires just the MCP server into `~/.codex/config.toml`.

```bash
mneme install --client=codex
```

This appends a managed `[mcp_servers.mneme]` block bracketed by sentinels,
so `mneme uninstall --client=codex` removes exactly mneme's lines and
leaves the rest of your config untouched. Use `--client=all` to wire both
Claude Code and Codex in one run.

## Prerequisites

Both paths reuse the shared `mneme` CLI and the `mneme-mcp` binary from the
Claude-Code-native core:

```bash
pipx install mneme-cc-plugin   # provides the `mneme` CLI (hook dispatch)
npm install -g mneme-mcp-server  # provides the mneme-mcp command
```

Published package versions are kept in lockstep with the repository status line. For unreleased changes, install from source in this monorepo.

## What works on Codex

The nine MCP tools (`mneme_search`, `mneme_recall`, `mneme_write`,
`mneme_prime`, `mneme_summarize`, `mneme_timeline`, `mneme_propose`,
`mneme_checkpoint_list`, `mneme_working_set_load`) and the two skills
(`mneme-prime`, `mneme-search`) are identical across clients. Four of the
six registered Claude Code hook events map to four native Codex events. A single
`mneme hook <event>` command serves both clients.

| Capability | Claude Code (native) | Codex (extended) |
|---|---|---|
| 9 MCP tools | full | full |
| Skills (prime, search) | full | full |
| SessionStart prime | full | full |
| PostToolUse capture | full | full (Bash-output compression is tuned to Claude Code tool names) |
| Stop deterministic append | full | full |
| PreCompact state save | full | full |
| SessionEnd flush | dedicated hook | folded into Stop (Codex has no SessionEnd event) |

## Design

The multi-client architecture is recorded in ADR-014 in
`docs/ARCHITECTURE.md`. The short version: keep the core untouched and
client-neutral, give both clients one shared hook entry, and ship a native
plugin per client. mneme stays Claude-Code-native by origin and gains Codex
as a parallel front-end.
