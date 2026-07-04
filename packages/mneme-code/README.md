# mneme-code

Deterministic Python code-failure memory for mneme. Parses CPython tracebacks
into structured failures, renders them as vault-ground-truth markdown memories,
and resolves stack frames to [mneme-graph](../mneme-graph) function nodes.

Part of the [mneme](https://github.com/OnourImpram/mneme) memory engine.

## What it does (v1)

- **Traceback parsing.** `parse_traceback` turns a standard CPython traceback
  into a `ParsedTraceback` (exception type, message, frames). It never raises;
  unrecognised input returns `None`.
- **Failure memories.** `failure_from_traceback` + `failure_to_markdown` produce
  a markdown note the user owns, with provenance (`content_hash`, `observed_at`,
  `trust`) and a confidence label.
- **Frame resolution.** `resolve_frames` pairs each frame with a mneme-graph
  `function` node when one matches, with a clean fallback to `None`.

Redaction (`mneme_core.privacy.redact`) is applied to every user-derived string
(exception message, code context, file path) before it is stored or rendered.
No LLM, no network; library functions are pure (the caller injects timestamps).

## Scope and deferrals (v1)

The following are **not** implemented yet and are documented here for honesty:

- Live test-runner integration.
- Branch-aware failure tracking.
- `AGENTS.md` procedural-memory parsing and repo runbook generation.
- Fix-trajectory wiring: a *fix* is modelled as a `mneme-temporal` claim that
  **supersedes** the failure memory. That lifecycle lives in `mneme-temporal`
  and is reused, not reinvented here.
- Non-Python tracebacks.
