"""PreCompact hook: snapshot the vault state file and write a CCE checkpoint.

Claude Code calls PreCompact right before context compaction. The
mneme hook responds by:

1. (CCE only, gated) Building and persisting a fresh checkpoint from the
   current session so the latest working-set state is captured before the
   host compacts the transcript.  Fail-soft — any error is swallowed and
   the stamp step always runs.
2. Stamping a ``last_precompact_at`` field into ``vault/.mneme/state.json``
   so the next SessionStart can detect the compaction and run self-heal.

The 500ms budget is respected: the checkpoint write is a fast markdown
file write + JSONL append (no LLM calls, no network).
"""

from __future__ import annotations

import json
import sys
from datetime import UTC, datetime
from typing import Any

from mneme_core.cce.build import build_checkpoint, write_checkpoint
from mneme_core.cce.config import read_config
from mneme_core.cce.triggers import read_cce_state
from mneme_core.vault.atomic_write import atomic_write_text
from mneme_core.vault.config import VaultConfig

from .lib import emit, run_hook

STATE_FILENAME = "state.json"


def _write_cce_checkpoint(event: dict[str, Any], vault: VaultConfig) -> None:
    """Write a CCE checkpoint if CCE is enabled.  Fail-soft."""
    try:
        cce_config = read_config(vault.cce_config_path)
        if not cce_config.enabled:
            return

        session_id = str(event.get("session_id") or "")
        transcript_path = str(event.get("transcript_path") or "")

        # Read the last checkpoint anchor so the chain is maintained.
        cce_state = read_cce_state(vault.state_dir)
        prev_anchor: str | None = cce_state.get("last_checkpoint_anchor") or None

        cp = build_checkpoint(vault, session_id, transcript_path, prev_anchor)
        write_checkpoint(vault, cp)
    except Exception as exc:
        sys.stderr.write(f"[mneme:PreCompact] CCE checkpoint failed: {exc}\n")


def handle(event: dict[str, Any], vault: VaultConfig | None) -> None:
    if vault is None:
        emit(hook_event_name="PreCompact")
        return

    # CCE: write a final checkpoint before the transcript is compacted.
    # Fail-soft: even if the checkpoint write raises the stamp must still run.
    try:
        _write_cce_checkpoint(event, vault)
    except Exception as exc:
        sys.stderr.write(f"[mneme:PreCompact] CCE checkpoint error: {exc}\n")

    state_path = vault.state_dir / STATE_FILENAME
    try:
        if state_path.exists():
            state = json.loads(state_path.read_text(encoding="utf-8"))
        else:
            state = {}
    except (OSError, json.JSONDecodeError):
        state = {}

    state["last_precompact_at"] = datetime.now(UTC).isoformat()
    state.setdefault("schema_version", 1)

    try:
        state_path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_text(
            state_path,
            json.dumps(state, indent=2, ensure_ascii=False) + "\n",
        )
    except OSError as exc:
        sys.stderr.write(f"[mneme:PreCompact] write failed: {exc}\n")

    emit(hook_event_name="PreCompact")


def main() -> int:
    return run_hook(handle, hook_event_name="PreCompact")


if __name__ == "__main__":
    sys.exit(main())
