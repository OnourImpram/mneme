# P2-008 — CCE Compaction-Recall Diagnostics

## Capability

Surface what was lost during context compaction, what was re-injected from
the checkpoint, and how much of the token budget each step consumed — giving
operators and hook scripts a structured diagnostic view instead of silent
best-effort recovery.

## Current State

`packages/mneme-core/src/mneme_core/cce/`:

- `loss_detect.py` — `detect_dropped(checkpoint, post_transcript_path)`
  (lines 46-85) returns a `tuple[WorkingSetItem, ...]` of items whose key
  was absent from the post-compaction transcript.  Return value carries
  per-item `salience` but no token counts and no re-injection record.
- `budget.py` — `estimate_context_fill(transcript_path, context_window_tokens)`
  (lines 32-91) returns a fill fraction `[0.0, 1.0]`.  Standalone; not
  connected to `detect_dropped` output.
- `build.py` — `build_checkpoint()` / `write_checkpoint()` write the
  checkpoint markdown.  No diagnostic output.
- The `Checkpoint` dataclass (`checkpoint.py`) carries `items` but no
  re-injection ledger.

There is no aggregated diagnostic structure; callers would have to call
`detect_dropped`, `estimate_context_fill`, and `load_latest_checkpoint`
separately and stitch results together.

## Proposed Design

### `CompactionDiagnostic` dataclass (`cce/diagnostic.py`)

```python
@dataclass(frozen=True)
class DroppedItem:
    kind: str          # "decision" | "todo" | "recent_edit" | "intent"
    text: str          # redacted text key
    salience: float
    estimated_tokens: int   # estimate_tokens(text) from budget.py

@dataclass(frozen=True)
class CompactionDiagnostic:
    session_id: str
    checkpoint_anchor: str
    # Loss
    dropped: tuple[DroppedItem, ...]
    dropped_token_estimate: int   # sum of DroppedItem.estimated_tokens
    # Re-injection
    reinjected_count: int         # items that were re-injected (caller-supplied)
    reinjected_token_estimate: int
    # Budget
    pre_compaction_fill: float    # fill fraction before compaction
    post_compaction_fill: float   # fill fraction after compaction
    context_window_tokens: int
```

### `build_diagnostic()` factory (`cce/diagnostic.py`)

```python
def build_diagnostic(
    vault: VaultConfig,
    session_id: str,
    post_transcript_path: Path,
    context_window_tokens: int,
    reinjected_items: Sequence[WorkingSetItem] = (),
) -> CompactionDiagnostic:
```

Internally calls:
1. `load_latest_checkpoint(vault.checkpoint_index)` → `Checkpoint`.
2. `detect_dropped(checkpoint, post_transcript_path)` → dropped items.
3. `estimate_context_fill(post_transcript_path, context_window_tokens)` →
   `post_compaction_fill`.
4. Estimates pre-compaction fill from the checkpoint's own item token sum
   relative to `context_window_tokens` (heuristic, same `estimate_tokens`
   helper from `budget.py`).

### CLI surface: `mneme cce diagnose`

```
mneme cce diagnose --vault PATH --session SESSION_ID
                   [--transcript PATH]
                   [--context-window 200000]
                   [--format json|text]
```

Text output example:

```
CCE diagnostic — session abc123
  Checkpoint  : 2025-06-15-20250615T143022Z-abc123
  Dropped     : 3 items (~420 tokens)
    [HIGH 0.92]  decision  "decided to use RRF fusion for..."
    [MED  0.61]  todo      "TODO: add scope migration test"
    [LOW  0.34]  intent    "ship the dense adapter stub"
  Re-injected : 1 item (~140 tokens)
  Fill before : 67%   after: 31%   window: 200 000 tokens
```

### Extension Point

`cce/loss_detect.py:46` (`detect_dropped`) and `cce/budget.py:32`
(`estimate_context_fill`) are the two building-block calls.  The new
`cce/diagnostic.py` module composes them; neither existing file changes.
The CLI group `cce` in `cli.py` gains a `diagnose` subcommand.

## Feature-Flag / Rollout Plan

`mneme cce diagnose` is a new opt-in command; existing `cce` subcommands
(`checkpoint`, `inject`) are unchanged.  The `CompactionDiagnostic`
dataclass is a pure data carrier with no side effects; it can be imported
and used by hook scripts independently of the CLI.  No default-behavior
change.
