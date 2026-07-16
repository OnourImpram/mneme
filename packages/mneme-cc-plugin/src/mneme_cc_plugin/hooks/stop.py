"""Stop hook: deterministic session log append.

The Zero-LLM-Stop sacred constraint says the Stop hook never calls an
LLM. The full Stop ritual is:

  1. If the vault has no untracked or modified files since the last
     known state, treat this as an empty session and return without
     writing.
  2. Otherwise, atomically append a new ``## HH:MM session summary``
     block to today's session log inside the vault. The body lists
     the session_id, the transcript path (if Claude Code surfaced
     one), and the deterministic extractive summary produced by
     ``mneme_core.distill.session_summary`` (zero-LLM, default ON,
     disable via ``summary.json``). When the summarizer has nothing
     to say it returns ``None`` and the body falls back to the
     editable placeholder line. The day's log file is created with
     ``type: session`` frontmatter so the indexer records
     ``frontmatter_type='session'`` and SessionStart can surface it
     in the recent-sessions block.
  3. Update a small ``last_session_end_at`` field in the vault's
     state file. The next SessionStart will read this.

Step 1 is implemented purely from filesystem mtimes inside the vault.
The git check is a soft optimization, not a requirement, so vaults
that are not git repositories still get a log entry.

The constants ``SESSION_LOG_HEADING`` and ``LOG_DIR_NAME`` are local
to keep the hook self-contained. Changing them in a future version
is a settings.json migration concern.
"""

from __future__ import annotations

import json
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from mneme_core.distill.session_summary import build_session_summary
from mneme_core.kg import kg_config_from_vault, mark_community_refresh
from mneme_core.vault.atomic_write import atomic_write_text
from mneme_core.vault.config import VaultConfig

from .lib import emit, run_hook
from .lock import file_lock

LOG_DIR_NAME = "sessions"
STATE_FILENAME = "state.json"
SESSION_LOG_LOCK_TIMEOUT_S = 5.0


def _is_empty_session(vault_root: Path) -> bool:
    """Best-effort: True when vault has no changes since last commit.

    Vaults that are not git repositories return False (i.e. always
    log something) because there is no cheap way to know what
    changed.
    """
    if not (vault_root / ".git").exists():
        return False
    try:
        result = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=str(vault_root),
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=3,
        )
    except (subprocess.TimeoutExpired, OSError, FileNotFoundError):
        return False
    return not bool(result.stdout.strip())


def _now_utc() -> datetime:
    return datetime.now(UTC)


def _append_session_section(
    vault: VaultConfig,
    session_id: str,
    transcript_path: str,
) -> None:
    today_iso = _now_utc().date().isoformat()
    target = vault.root / LOG_DIR_NAME / f"{today_iso}.md"
    # Deterministic extractive summary (zero-LLM, default ON). Computed
    # OUTSIDE the lock so summary aggregation never extends the critical
    # section; the function degrades to None instead of raising.
    summary_body = build_session_summary(vault, session_id, transcript_path)
    # Codex Pass 2 review fix: two concurrent Stop hooks read the same
    # file then both atomic-write their appended section, with the
    # later writer overwriting the earlier session block. Serialize the
    # read-modify-write under a sidecar lockfile so the operation is
    # atomic across processes. Lock files live under .mneme/locks so
    # the vault markdown tree stays clean.
    lock_path = (
        vault.state_dir / "locks" / f"{LOG_DIR_NAME}-{today_iso}.lock"
    )
    ts_hhmm = _now_utc().strftime("%H:%M")
    with file_lock(lock_path, timeout_s=SESSION_LOG_LOCK_TIMEOUT_S):
        if target.exists():
            existing = target.read_text(encoding="utf-8")
        else:
            # New daily log: write session-typed frontmatter so the
            # indexer sets frontmatter_type='session' and SessionStart
            # can discover it. Built inline to keep the Stop hot path
            # free of the yaml-backed serializer import.
            created_iso = _now_utc().isoformat()
            existing = (
                "---\n"
                f"id: session-{today_iso}\n"
                "type: session\n"
                f"created: {created_iso}\n"
                "schema_version: 1\n"
                "---\n"
                f"# Sessions {today_iso}\n\n"
            )
        separator = "" if existing.endswith("\n\n") else "\n\n"
        header = f"## {ts_hhmm} session {session_id}"
        transcript_line = (
            f"transcript: `{transcript_path}`\n\n" if transcript_path else ""
        )
        section_body = (
            summary_body
            if summary_body
            else "(Session summary placeholder. Edit to add notes.)\n"
        )
        body = (
            f"{existing}{separator}"
            f"{header}\n\n"
            f"{transcript_line}"
            f"{section_body}"
        )
        atomic_write_text(target, body)


def _touch_state(vault: VaultConfig) -> None:
    state_path = vault.state_dir / STATE_FILENAME
    try:
        if state_path.exists():
            state = json.loads(state_path.read_text(encoding="utf-8"))
        else:
            state = {}
    except (OSError, json.JSONDecodeError):
        state = {}
    state["last_session_end_at"] = _now_utc().isoformat()
    state.setdefault("schema_version", 1)
    state_path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(
        state_path,
        json.dumps(state, indent=2, ensure_ascii=False) + "\n",
    )


def handle(event: dict[str, Any], vault: VaultConfig | None) -> None:
    if vault is None:
        emit(hook_event_name="Stop")
        return

    if _is_empty_session(vault.root):
        try:
            _touch_state(vault)
        except OSError as exc:
            sys.stderr.write(f"[mneme:Stop] state touch failed: {exc}\n")
        emit(hook_event_name="Stop")
        _request_kg_community_refresh(vault)
        return

    session_id = str(event.get("session_id") or "unknown")
    transcript_path = str(event.get("transcript_path") or "")
    try:
        _append_session_section(vault, session_id, transcript_path)
        _touch_state(vault)
    except OSError as exc:
        sys.stderr.write(f"[mneme:Stop] write failed: {exc}\n")
    emit(hook_event_name="Stop")
    _request_kg_community_refresh(vault)


def _request_kg_community_refresh(vault: VaultConfig) -> None:
    """Mark a community-refresh request for the next KG worker drain.

    Gated by kg_active_flag inside mark_community_refresh itself, so
    this is a cheap no-op when the full profile is not provisioned.
    """
    try:
        mark_community_refresh(kg_config_from_vault(vault))
    except Exception as exc:
        sys.stderr.write(f"[mneme:Stop] kg refresh skipped: {exc}\n")


def main() -> int:
    return run_hook(handle, hook_event_name="Stop")


if __name__ == "__main__":
    sys.exit(main())
