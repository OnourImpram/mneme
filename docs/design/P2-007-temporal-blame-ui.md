# P2-007 — Temporal Blame UI: Color, Paging, Timeline

## Capability

Replace the plain-text `mneme temporal blame` output with a richer
presentation: ANSI color coding by claim status, paging via `$PAGER`,
and an ASCII timeline of the supersession chain.

## Current State

`packages/mneme-core/src/mneme_core/temporal/blame.py`:

- `BlameReport` (lines 39-45) — frozen dataclass: `target: Claim`,
  `ancestors: list[Claim]`, `descendants: list[Claim]`, `rivals: list[Claim]`.
- `blame()` (lines 114-137) — returns `list[BlameReport]`.  Pure SQLite,
  no output formatting.

`packages/mneme-core/src/mneme_core/cli.py`:

- `temporal_blame()` CLI handler (line 1462) — calls `blame()`, serializes
  to JSON, writes to stdout.  Currently JSON-only output; no ANSI, no pager,
  no timeline rendering.

The claims carry `observed_at`, `supersedes`, and `claim_key` fields (see
`temporal/claim.py`) which are sufficient for timeline construction.

## Proposed Design

### Renderer module: `temporal/blame_render.py`

```python
def render_blame_report(
    report: BlameReport,
    *,
    color: bool = True,
    timeline: bool = True,
) -> str:
```

**Color coding** (ANSI, degraded to plain when `color=False` or
`NO_COLOR` env var is set):
- Active claim (no descendant): green header.
- Superseded claim (has descendant): dim/strikethrough.
- Rival claim (same `claim_key`, different id): yellow.
- Ancestor chain label: blue prefix.

**Timeline** (ASCII, `timeline=True`):

```
[2025-03-01]  claim_abc  "session goal: ship feature X"
      │
      ▼ superseded by
[2025-04-10]  claim_def  "session goal: ship feature X v2"  ← CURRENT
```

Each node shows `observed_at[:10]`, `claim_id[:8]`, and `text[:60]`.
The chain is capped at `_MAX_CHAIN = 64` (matching `blame.py:36`).

**Paging**: when output exceeds terminal height, pass rendered text to
`subprocess.run(["less", "-R"], input=..., text=True)` where `-R` preserves
ANSI codes.  Pager is skipped when stdout is not a TTY (`sys.stdout.isatty()
== False`), when `MNEME_NO_PAGER=1`, or when `less` is not on PATH (falls
back to plain print).

### CLI integration

`temporal_blame()` in `cli.py:1462` gains two flags:

```
--format [json|text]   default: text
--no-color             disable ANSI
--no-pager             disable pager
```

The `--format json` path is unchanged for scripting compatibility.

### Extension Point

`cli.py:1462` — `temporal_blame` handler; new renderer is a separate module
`temporal/blame_render.py` so it is independently testable without Click.

## Feature-Flag / Rollout Plan

`--format text` is the new default.  Existing scripts that rely on JSON
output pass `--format json` explicitly (one-time flag addition).  ANSI is
disabled automatically on non-TTY stdout and via `NO_COLOR` / `MNEME_NO_PAGER`
env vars — no config change required.
