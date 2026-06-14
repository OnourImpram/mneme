"""UserPromptSubmit hook: CCE context-fill check and checkpoint trigger.

Fires before each user message is processed by Claude Code. If the CCE
is enabled in the vault config, this hook:

  1. Estimates the current context fill from the transcript JSONL.
  2. Consults ``cce.triggers.should_checkpoint`` (pure, no IO).
  3. On trigger, builds and writes a checkpoint via ``cce.build``.
  4. Increments the debounce counter in state.json.

The hook is fail-soft: any exception is caught and discarded so Claude
Code is never blocked. The CCE is opt-in (default ``enabled = false``),
so when disabled the hook exits with zero overhead after a single config
read.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

from mneme_core.cce.budget import estimate_context_fill
from mneme_core.cce.build import build_checkpoint, write_checkpoint
from mneme_core.cce.config import read_config
from mneme_core.cce.triggers import (
    increment_event_counter,
    read_cce_state,
    should_checkpoint,
    update_cce_state,
)
from mneme_core.vault.config import VaultConfig

from .lib import emit, run_hook


def handle(event: dict[str, Any], vault: VaultConfig | None) -> None:
    if vault is None:
        emit(hook_event_name="UserPromptSubmit")
        return

    try:
        config = read_config(vault.cce_config_path)
        if not config.enabled:
            emit(hook_event_name="UserPromptSubmit")
            return

        # Increment the debounce counter and read current state.
        events_since = increment_event_counter(vault.state_dir)
        state = read_cce_state(vault.state_dir)
        prev_anchor: str | None = state.get("last_checkpoint_anchor")

        # Derive the transcript path from the event (may be absent).
        transcript_path_str = str(event.get("transcript_path") or "")
        transcript_path = Path(transcript_path_str) if transcript_path_str else Path()

        fill_pct = estimate_context_fill(transcript_path, config.context_window_tokens)

        trigger, reason = should_checkpoint(
            fill_pct=fill_pct,
            config=config,
            event=event,
            events_since_last=events_since,
        )

        if trigger:
            session_id = str(event.get("session_id") or "unknown")
            cp = build_checkpoint(vault, session_id, transcript_path_str, prev_anchor)
            write_checkpoint(vault, cp)
            update_cce_state(
                vault.state_dir,
                last_checkpoint_anchor=cp.anchor,
                events_since_checkpoint=0,
            )
            sys.stderr.write(
                f"[mneme:UserPromptSubmit] checkpoint {cp.anchor} written"
                f" (reason={reason})\n"
            )

    except Exception as exc:
        sys.stderr.write(f"[mneme:UserPromptSubmit] skipped: {exc}\n")

    emit(hook_event_name="UserPromptSubmit")


def main() -> int:
    return run_hook(handle, hook_event_name="UserPromptSubmit")


if __name__ == "__main__":
    sys.exit(main())
