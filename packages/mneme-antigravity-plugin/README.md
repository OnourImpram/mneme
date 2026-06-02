# mneme for Antigravity

mneme is vault-native memory, born Claude-Code-native and extended to
Antigravity (Google's agentic IDE). The retrieval core (`mneme-core`),
the MCP server (`mneme-mcp`), and the vault markdown contract are
client-neutral, so Antigravity gets the same six tools and the same
vault with no loss of fidelity.

## What this extension wires into Antigravity

- **MCP server** (`mneme-mcp`): six tools — `mneme_search`,
  `mneme_recall`, `mneme_write`, `mneme_prime`, `mneme_summarize`,
  `mneme_timeline`.
- **Skills**: `mneme-prime` (preflight context) and `mneme-search`
  (vault recall).
- **Lifecycle hooks** via the shared `mneme hook <event>` command:
  SessionStart (prime context), PostToolUse (stage events), Stop
  (deterministic append, no LLM), PreCompact (state save).
- **Context rules** in `GEMINI.md`: tool reference and ground-truth
  discipline injected into every Antigravity session.

Antigravity has no dedicated SessionEnd event. The Stop hook absorbs
session-end flushing (same model as the Codex plugin).

## Prerequisites

```bash
pipx install mneme-cc-plugin   # provides the `mneme` CLI (hook dispatch)
npm install -g mneme-mcp-server  # provides the mneme-mcp command
```

## Install

```bash
mneme install --client antigravity --vault ~/mneme-vault
```

This materialises the extension into `~/.gemini/extensions/mneme/`
with `MNEME_VAULT` set to the resolved vault path.

To remove:

```bash
mneme uninstall --client antigravity
```

## Coverage versus Claude Code

| Capability | Claude Code (native) | Antigravity (extended) |
|---|---|---|
| 6 MCP tools | full | full |
| Skills (prime, search) | full | full |
| SessionStart prime | full | full |
| PostToolUse capture | full | full |
| Stop deterministic append | full | full |
| PreCompact state save | full | full |
| SessionEnd flush | dedicated hook | folded into Stop (no SessionEnd event) |
