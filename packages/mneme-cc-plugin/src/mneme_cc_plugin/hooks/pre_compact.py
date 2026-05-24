"""PreCompact hook: snapshot the vault state file.

Claude Code calls PreCompact right before context compaction. The
mneme hook responds by stamping a `last_precompact_at` field into
``vault/.mneme/state.json`` so the next SessionStart can decide
whether to refresh its preamble. The hook does no other work; the
500ms budget would not survive a heavier touch.
"""

from __future__ import annotations

import json
import sys
from datetime import UTC, datetime
from typing import Any

from mneme_core.vault.atomic_write import atomic_write_text
from mneme_core.vault.config import VaultConfig

from .lib import emit, run_hook

STATE_FILENAME = "state.json"


def handle(event: dict[str, Any], vault: VaultConfig | None) -> None:
    if vault is None:
        emit(hook_event_name="PreCompact")
        return

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
