"""SessionEnd hook: flush staging buffers and (opt-in) compression.

In the lite profile this is a near no-op: it stamps
``last_session_end_at`` into the vault state file so SessionStart
can compute the elapsed-since-last-session window. Full profile
will later trigger an asynchronous Graphiti episode flush. That
flush lives behind the install profile gate and is not invoked
from here at v1.0.

Background compression, when opted into by the user, is launched
as a fire-and-forget subprocess so the hook still returns inside
its latency budget. v1.0 ships compression OFF by default so this
hook does not invoke any LLM unprompted. The gating is layered:

  1. The compression flag must be true in the vault config.
  2. The pause flag must not be present.
  3. The Anthropic key (or any compatible LLM auth) must be in env.

The subprocess inherits the parent env so the user does not need
to re-export the key, and the spawn is detached so the hook
returns immediately. Codex Pass 2 review fix: prior implementation
documented this behavior but never spawned the subprocess.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import UTC, datetime
from typing import Any

from mneme_core.vault.atomic_write import atomic_write_text
from mneme_core.vault.config import VaultConfig

from .lib import emit, run_hook

STATE_FILENAME = "state.json"

# Subprocess launch flags that disconnect the child from the hook so
# the hook returns inside its latency budget while the LLM call runs.
if sys.platform == "win32":
    _DETACHED_FLAGS = 0x00000008 | 0x00000200  # DETACHED | NEW_PROCESS_GROUP
else:
    _DETACHED_FLAGS = 0


def _compression_enabled(vault: VaultConfig) -> bool:
    """Read the vault compression config flag without importing LLM deps.

    Returns False if the file is missing or malformed so opt-out
    behavior survives any config-side drift.
    """
    cfg_path = vault.compression_config_path
    if not cfg_path.is_file():
        return False
    try:
        data = json.loads(cfg_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    return bool(data.get("enabled", False))


def _has_provider_auth() -> bool:
    """Cheap env check so we never spawn when the LLM call would fail."""
    for var in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "MNEME_LLM_API_KEY"):
        if os.environ.get(var):
            return True
    return False


def _maybe_launch_compression(vault: VaultConfig) -> None:
    """Fire-and-forget background compression run when opt-in gates pass."""
    if not _compression_enabled(vault):
        return
    if vault.compression_pause_flag.is_file():
        return
    if not _has_provider_auth():
        return
    try:
        cmd = [
            sys.executable,
            "-m",
            "mneme_core.cli",
            "compress",
            "run",
        ]
        popen_kwargs: dict[str, Any] = {
            "cwd": str(vault.root),
            "stdout": subprocess.DEVNULL,
            "stderr": subprocess.DEVNULL,
            "stdin": subprocess.DEVNULL,
            "close_fds": True,
        }
        if sys.platform == "win32":
            popen_kwargs["creationflags"] = _DETACHED_FLAGS
        else:
            popen_kwargs["start_new_session"] = True
        subprocess.Popen(cmd, **popen_kwargs)
    except Exception as exc:
        sys.stderr.write(
            f"[mneme:SessionEnd] compression launch skipped: {exc}\n"
        )


def _maybe_drain_proposals(vault: VaultConfig) -> None:
    """Apply queued memory proposals under the operator's policy.

    Deterministic file IO only (no LLM, no network), so it is safe on
    the SessionEnd path. Runs only when both a policy file and a
    pending queue exist; everything else is a no-op. Fail-soft: a
    drain error never blocks the hook.
    """
    policy_path = vault.state_dir / "policy.json"
    queue_path = vault.state_dir / "proposals" / "pending.jsonl"
    if not policy_path.is_file() or not queue_path.is_file():
        return
    try:
        from mneme_core.memory_apply import drain_proposals

        report = drain_proposals(vault)
        if report.applied or report.refused:
            sys.stderr.write(
                f"[mneme:SessionEnd] proposal drain: applied={report.applied} "
                f"refused={report.refused} malformed={report.malformed}\n"
            )
    except Exception as exc:
        sys.stderr.write(f"[mneme:SessionEnd] proposal drain skipped: {exc}\n")


def handle(event: dict[str, Any], vault: VaultConfig | None) -> None:
    if vault is None:
        emit(hook_event_name="SessionEnd")
        return

    _maybe_drain_proposals(vault)

    state_path = vault.state_dir / STATE_FILENAME
    try:
        if state_path.exists():
            state = json.loads(state_path.read_text(encoding="utf-8"))
        else:
            state = {}
    except (OSError, json.JSONDecodeError):
        state = {}

    state["last_session_end_at"] = datetime.now(UTC).isoformat()
    state.setdefault("schema_version", 1)

    try:
        state_path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_text(
            state_path,
            json.dumps(state, indent=2, ensure_ascii=False) + "\n",
            vault_root=vault.root,
        )
    except OSError as exc:
        sys.stderr.write(f"[mneme:SessionEnd] write failed: {exc}\n")

    # Three-layer gate enforced inside _maybe_launch_compression.
    _maybe_launch_compression(vault)

    emit(hook_event_name="SessionEnd")


def main() -> int:
    return run_hook(handle, hook_event_name="SessionEnd")


if __name__ == "__main__":
    sys.exit(main())
