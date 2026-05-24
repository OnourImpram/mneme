# mneme for Codex

mneme is vault-native memory, born Claude-Code-native and extended to
the OpenAI Codex CLI. The retrieval core (`mneme-core`), the MCP server
(`mneme-mcp`), and the vault markdown contract are client-neutral, so
Codex gets the same tools and the same vault with no loss of fidelity.

## What this plugin wires into Codex

- **MCP server** (`mneme-mcp`): six tools, `mneme_search`,
  `mneme_recall`, `mneme_write`, `mneme_prime`, `mneme_summarize`,
  `mneme_timeline`.
- **Skills**: `mneme-prime` (preflight context) and `mneme-search`
  (vault recall).
- **Lifecycle hooks** via the shared `mneme hook <event>` command:
  SessionStart (prime context), PostToolUse (stage events), Stop
  (deterministic append, no LLM), PreCompact (state save).

## Prerequisites

The hooks and the MCP server reuse the shared `mneme` CLI and the
`mneme-mcp` binary from the Claude-Code-native core:

```bash
pipx install mneme-cc-plugin   # provides the `mneme` CLI (hook dispatch)
npm install -g mneme-mcp       # provides the MCP server binary
```

These publish with mneme v1.0.0. Until then, install from source in
this monorepo.

## Install

```bash
codex plugin marketplace add TheGoatPsy/mneme
# then enable the mneme plugin and trust its hooks when prompted
```

Codex treats plugin-bundled hooks as non-managed, so you review and
trust the hook definitions on first run.

## Coverage versus Claude Code

mneme stays Claude-Code-native by origin. Codex support is an additive
layer over the same client-neutral core.

| Capability | Claude Code (native) | Codex (extended) |
|---|---|---|
| 6 MCP tools | full | full |
| Skills (prime, search) | full | full |
| SessionStart prime | full | full |
| PostToolUse capture | full | full (Bash-output compression is tuned to Claude Code tool names) |
| Stop deterministic append | full | full |
| PreCompact state save | full | full |
| SessionEnd flush | dedicated hook | folded into Stop (Codex has no SessionEnd event) |
