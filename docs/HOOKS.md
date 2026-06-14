# Hook Integration Guide

mneme registers five Claude Code hooks. Each has a strict latency budget and a documented purpose.

## Registered Hooks

| Hook | File | Budget | Seeded p95 reference | Purpose |
|---|---|---|---|---|
| `PostToolUse` | `hooks/post_tool_use.py` | non-blocking | n/a (async) | Capture tool input/output, compress long Bash stdout/stderr with `distill.shell_compress`, stage for indexing, append to the KG queue when the full-profile flag is active. |
| `SessionStart` | `hooks/session_start.py` | 500 ms p95 | 3 ms (retrieve over 500-doc corpus) | Inject preflight vault context with `distill.injection_dedup`. |
| `Stop` | `hooks/stop.py` | 1000 ms p95 | **2 ms** (Benchmark B, seed 42) | Append session summary deterministically. No LLM call. |
| `PreCompact` | `hooks/pre_compact.py` | 200 ms p95 | sub-millisecond | Snapshot pre-compaction state for recovery. |
| `SessionEnd` | `hooks/session_end.py` | 500 ms p95 | sub-millisecond | Stamp session state and launch opt-in background compression only when compression is enabled, no pause flag is present, and provider auth is in env. |
| `UserPromptSubmit` | `hooks/user_prompt_submit.py` | non-blocking | sub-millisecond (gated) | Evaluate whether the current context fill or event type warrants a checkpoint; if so, build and persist a working-set checkpoint. Gated behind `CceConfig.enabled` (default off). |

Latency reference numbers come from `benchmarks/latency/run.py` on operator hardware (Windows 11, Python 3.13, NTFS SSD), seeded with `MNEME_BENCH_SEED=42`. CI guard at `benchmarks/latency/p95_guard.py` enforces the 1000 ms Stop budget.

## Windows Considerations

Hooks invoke Python via `py -3` (the Python launcher) on Windows, never bare `python`. The bare command resolves to the Windows App Execution Alias stub on default installs and fails silently with an exit code that the hook framework interprets as success.

```python
# Hook launcher (cross-platform)
import sys, subprocess
launcher = "py" if sys.platform == "win32" else "python3"
launcher_args = ["-3"] if sys.platform == "win32" else []
subprocess.run([launcher, *launcher_args, str(hook_path), *sys.argv[1:]])
```

## BOM-Safe settings.json Mutation

Claude Code's `settings.json` on Windows is sometimes saved with a UTF-8 BOM. mneme's installer reads, mutates, and writes the file preserving the BOM exactly. Naive `json.load` and `json.dump` strip the BOM, breaking subsequent reads by tools that expect it.

See `packages/mneme-cc-plugin/src/mneme_cc_plugin/install/settings.py` for the load-write helpers used by `mneme install`.

## Deterministic Session Summary (default ON)

The Stop hook fills each session-log entry with an **extractive,
zero-LLM summary**: files touched, tool activity counts, and the
session's opening intent (a bounded read of the transcript head). The
summary is computed from the already-redacted PostToolUse staging
records and is passed through `privacy.redact` once more before it is
written, so `<private>` content can never reach the session log. No
LLM, no network, no API key — the Zero-LLM-Stop constraint holds.

Configure via `<vault>/.mneme/summary.json`:

```json
{"deterministic": true, "language": "en"}
```

`deterministic: false` restores the editable placeholder line.
`language` selects a localized template (`en` and `tr` ship today);
unknown values fall back to English. The opt-in LLM compression
pipeline is unchanged and layers richer observations on top — it also
honours the same `language` knob via `compression.json` for its
localized rubric presets (`compress-en.md`, `compress-tr.md`).

## Append, Not Replace

Hook entries are merged additively. mneme never replaces user-configured hooks. If a hook key already exists, mneme prepends its entry to the chain.

## Fail-Soft Contract

Hooks never block Claude Code. Every hook entry is wrapped by `mneme_cc_plugin.hooks.lib.run_hook`, which:

1. Runs the handler inside a try/except.
2. Captures any exception, writes it to stderr, and exits safely.
3. Exits with code 0 regardless of internal failure.

The contract is: a broken mneme never breaks the user's session. The cost is silent failure under bugs, which is why `mneme doctor` exists and is the first triage step for any reported issue.

## Disabling mneme Temporarily

```bash
# Disable all hooks (every mneme hook exits 0 immediately)
MNEME_DISABLED=1

# Skip specific hooks while keeping others (comma-separated)
MNEME_SKIP_HOOKS=PostToolUse,SessionEnd
```

When `MNEME_DISABLED` is truthy, all mneme hooks exit immediately with code 0 (success), leaving Claude Code unaffected. The narrower `MNEME_SKIP_HOOKS` accepts a comma-separated list of hook names for selective bypass during debugging.

## Three-Layer Gate Pattern

Optional integrations (knowledge graph, compression, dense embeddings) follow a uniform three-layer gate. In v1.0, dense retrieval remains a roadmap adapter. KG and compression gates are shipped.

1. **Config flag** (`compression_enabled`, `kg_enabled`, etc.) must be `true`.
2. **Lazy import** of the heavy dependency only when the flag is on.
3. **try/except** around the dependency call so a missing wheel or runtime error degrades to no-op rather than crash.

This is what lets lite-profile installs run with zero Neo4j and zero LLM SDK on disk.

## Uninstall

```bash
mneme uninstall
```

Removes hook entries from `settings.json` and preserves all non-mneme entries. The command leaves the vault directory and `.mneme/` indexes untouched so uninstall is reversible.

## Context Continuity Engine (CCE) Hook Integration

The CCE adds `UserPromptSubmit` as a sixth hook, registered only when `CceConfig.enabled` is `true` (default `false`). The remaining five hooks each gained a CCE integration path, also gated behind the same config flag.

**UserPromptSubmit** (`hooks/user_prompt_submit.py`): fires on every user prompt. The hook estimates current context fill from the JSONL transcript and evaluates the trigger hierarchy in `triggers.should_checkpoint`: explicit keyword ("remember this" / "hatirla") > fill threshold (default 65%) > git commit detected in a recent Bash response > large tool response (>8 KB serialized). When a trigger fires and the debounce counter allows it, `build.build_checkpoint` extracts touched files, decisions, and TODOs from the staging records, and `build.write_checkpoint` persists the checkpoint as a plain markdown file in `vault/.mneme/checkpoints/` and appends a line to the JSONL index. If CCE is not enabled the hook exits 0 immediately with no work done.

**PostToolUse**: the existing capture path gains two CCE trigger checks. A tool response whose serialized size exceeds 8 KB sets the large-response trigger flag in session state. A Bash tool response that contains a git commit SHA pattern sets the git-commit trigger flag. Both flags are consumed on the next `UserPromptSubmit` evaluation.

**SessionStart**: after the normal preflight vault injection, the CCE path runs `loss_detect.load_latest_checkpoint` to read the most recent checkpoint from the JSONL index. It then runs `loss_detect.detect_dropped` to compare the checkpoint items against the current transcript, identifying any items the host's post-compaction summary dropped. Dropped items are scored by `salience.score_item`, sorted descending, and re-injected into the session preamble within the configured token budget (default 4 000 tokens). If no checkpoint exists, or no items were dropped, the path is a no-op.

**PreCompact**: before the host compacts, this hook builds and persists a working-set checkpoint unconditionally (no trigger evaluation needed — the compaction event is itself the trigger). This ensures the most current working set is captured regardless of whether the fill threshold or a salient event already fired earlier in the session.

**All CCE paths are fail-soft.** Every CCE code path is wrapped in a try/except block and always exits 0. A broken CCE never breaks the user's session. The CCE does not control or trigger the host's compaction; it makes the aftermath recoverable.
