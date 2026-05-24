"""Shared plumbing for Claude Code hook implementations.

The functions here implement the lifecycle every hook follows:

  1. ``is_disabled``    Check the kill switch first.
  2. ``read_event``     Parse JSON from stdin, tolerating empty input.
  3. ``resolve_vault``  Locate the VaultConfig, returning None on miss.
  4. (do hook work)
  5. ``emit``           Write the success/error JSON envelope.

Hook handlers wrap their work in ``run_hook`` which centralizes the
failure-soft contract: any exception inside ``handler`` is caught and
converted to a benign success response. Claude Code never sees a hook
crash from mneme.
"""

from __future__ import annotations

import json
import os
import sys
import traceback
from collections.abc import Callable
from typing import Any

from mneme_core.vault.config import VaultConfig, VaultNotFoundError

KILL_SWITCH_ENV = "MNEME_DISABLED"


def is_disabled() -> bool:
    """Return True if the kill switch env var is set to a truthy value."""
    raw = os.environ.get(KILL_SWITCH_ENV, "")
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def read_event() -> dict[str, Any]:
    """Read the Claude Code hook event from stdin.

    The contract: Claude Code writes a JSON object to the hook stdin
    that contains at least ``hook_event_name``. Older or sparser
    versions may pass an empty stdin; tolerate that by returning an
    empty dict.
    """
    if sys.stdin.isatty():
        return {}
    raw = sys.stdin.read()
    if not raw or not raw.strip():
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def emit(
    *,
    continue_: bool = True,
    suppress_output: bool = True,
    additional_context: str | None = None,
    system_message: str | None = None,
    hook_event_name: str | None = None,
) -> None:
    """Write the hook response JSON envelope to stdout.

    ``additional_context`` is wrapped inside ``hookSpecificOutput`` as
    Claude Code expects when surfacing extra context.
    """
    payload: dict[str, Any] = {
        "continue": continue_,
        "suppressOutput": suppress_output,
    }
    if system_message is not None:
        payload["systemMessage"] = system_message
    if additional_context is not None:
        payload["hookSpecificOutput"] = {
            "hookEventName": hook_event_name or "Unknown",
            "additionalContext": additional_context,
        }
    sys.stdout.write(json.dumps(payload, ensure_ascii=False))
    sys.stdout.flush()


def resolve_vault() -> VaultConfig | None:
    """Resolve VaultConfig, returning None if no vault could be found.

    Hooks are fail-soft: a missing vault is not a crash, it is a
    no-op. The user runs ``mneme install`` to provision one.
    """
    try:
        return VaultConfig.resolve()
    except VaultNotFoundError:
        return None
    except Exception:
        return None


def run_hook(
    handler: Callable[[dict[str, Any], VaultConfig | None], None],
    *,
    hook_event_name: str,
) -> int:
    """Execute a hook handler with kill-switch + fail-soft semantics.

    Args:
        handler: function ``(event, vault) -> None``. It owns calling
            ``emit`` itself (so it can attach hook-specific output).
        hook_event_name: name reported in error envelopes for triage.

    Returns:
        Always 0. Non-zero exit codes from hooks risk being treated
        as a block by some Claude Code versions.
    """
    try:
        if is_disabled():
            emit(hook_event_name=hook_event_name)
            return 0
        event = read_event()
        vault = resolve_vault()
        handler(event, vault)
    except SystemExit:
        raise
    except BaseException:
        # Surface failure to stderr for operator debugging without
        # blocking Claude Code. stdout still gets a benign success.
        sys.stderr.write(f"[mneme:{hook_event_name}] {traceback.format_exc()}")
        emit(hook_event_name=hook_event_name)
    return 0
