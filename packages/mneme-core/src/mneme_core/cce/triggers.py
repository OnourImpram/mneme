"""CCE checkpoint trigger logic and state.json helpers.

``should_checkpoint`` is a pure function: all mutable state lives in the
caller (via the state.json read/update helpers below). The function never
touches the filesystem itself so it is easy to unit-test.

Trigger conditions (evaluated in order):
1. Explicit user request: "remember this" or "hatirla" in a UserPromptSubmit
   prompt. Bypasses the debounce counter.
2. Context fill >= config.context_threshold_pct ("threshold").
3. Salient event heuristics:
   - Bash tool with "git commit" in the command.
   - Large tool response (> 8 KB).
   All salient-event triggers respect the debounce counter.

The debounce counter (events_since_last) is read from state.json by the
caller and incremented there too; this module only consults the value.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ..vault.atomic_write import atomic_write_text
from .config import CceConfig

# Size threshold for "large tool response" heuristic (bytes of JSON).
_LARGE_RESPONSE_BYTES = 8 * 1024

# Keywords that override the debounce — user is explicitly asking to save.
_EXPLICIT_KEYWORDS = ("remember this", "hatirla")

STATE_FILENAME = "state.json"


# ---------------------------------------------------------------------------
# Pure trigger function
# ---------------------------------------------------------------------------


def should_checkpoint(
    *,
    fill_pct: float,
    config: CceConfig,
    event: dict[str, Any],
    events_since_last: int,
) -> tuple[bool, str]:
    """Decide whether the current event should trigger a checkpoint.

    Args:
        fill_pct: estimated context fill fraction in [0.0, 1.0].
        config: resolved CCE configuration.
        event: the raw Claude Code hook event dict.
        events_since_last: number of events processed since the last
            checkpoint was written (0 = immediately after a checkpoint).

    Returns:
        ``(trigger, reason)`` where ``trigger`` is True when a checkpoint
        should be written and ``reason`` is a short human-readable label.
    """
    # Check for explicit user keyword first — bypasses debounce entirely.
    if _is_explicit_request(event):
        return True, "explicit"

    # All other triggers respect the debounce window.
    if events_since_last < config.min_checkpoint_interval_events:
        return False, "debounced"

    if fill_pct >= config.context_threshold_pct:
        return True, "threshold"

    if _is_git_commit(event):
        return True, "git_commit"

    if _is_large_response(event):
        return True, "large_response"

    return False, ""


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------


def _is_explicit_request(event: dict[str, Any]) -> bool:
    """True when the event is a UserPromptSubmit containing a save keyword."""
    if event.get("hook_event_name") != "UserPromptSubmit":
        return False
    prompt = str(event.get("prompt") or "").lower()
    return any(kw in prompt for kw in _EXPLICIT_KEYWORDS)


def _is_git_commit(event: dict[str, Any]) -> bool:
    """True when the event is a Bash tool_use containing 'git commit'."""
    if event.get("tool_name") != "Bash":
        return False
    tool_input = event.get("tool_input")
    if not isinstance(tool_input, dict):
        return False
    command = str(tool_input.get("command") or "")
    return "git commit" in command


def _is_large_response(event: dict[str, Any]) -> bool:
    """True when the tool_response serialises to more than 8 KB."""
    response = event.get("tool_response")
    if response is None:
        return False
    try:
        size = len(json.dumps(response, ensure_ascii=False).encode("utf-8"))
    except (TypeError, ValueError):
        return False
    return size > _LARGE_RESPONSE_BYTES


# ---------------------------------------------------------------------------
# State.json helpers (debounce counter + last checkpoint metadata)
# ---------------------------------------------------------------------------


def read_cce_state(state_dir: Path) -> dict[str, Any]:
    """Read CCE fields from state.json, returning safe defaults on any error."""
    path = state_dir / STATE_FILENAME
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            return {}
        return raw
    except (OSError, json.JSONDecodeError):
        return {}


def update_cce_state(
    state_dir: Path,
    *,
    last_checkpoint_anchor: str | None = None,
    events_since_checkpoint: int | None = None,
) -> None:
    """Merge CCE counter fields into state.json atomically.

    Only the supplied keyword arguments are written; other state.json
    fields are preserved.  Missing keys default to safe values on read.
    """
    path = state_dir / STATE_FILENAME
    try:
        existing: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(existing, dict):
            existing = {}
    except (OSError, json.JSONDecodeError):
        existing = {}

    if last_checkpoint_anchor is not None:
        existing["last_checkpoint_anchor"] = last_checkpoint_anchor
    if events_since_checkpoint is not None:
        existing["events_since_checkpoint"] = events_since_checkpoint
    existing.setdefault("schema_version", 1)

    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(path, json.dumps(existing, indent=2, ensure_ascii=False) + "\n")


def increment_event_counter(state_dir: Path) -> int:
    """Increment events_since_checkpoint by 1 and return the new value.

    Reads the current value from state.json, bumps it atomically, and
    persists the updated state.  Returns the *new* counter value so the
    caller can pass it to ``should_checkpoint``.
    """
    state = read_cce_state(state_dir)
    current = int(state.get("events_since_checkpoint", 0))
    new_val = current + 1
    update_cce_state(state_dir, events_since_checkpoint=new_val)
    return new_val
