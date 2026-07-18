"""SessionStart hook: inject preflight vault context.

Returns up to ``MAX_CHARS`` characters of markdown that Claude Code
surfaces as conversation preamble. The bundle contains:

  - Today's date.
  - The last few headings from any session document modified today.
  - A short git status summary if the vault is a git repository.
  - The five most recently modified session-typed documents.
  - (CCE only) A self-heal block listing items dropped by the last
    compaction, recovered from the most recent checkpoint.

Each block is optional. If any source is missing the block is
silently omitted rather than reported as an error. The hook's job is
to make the new session smarter, not to surface diagnostic noise.

Heavy retrieval (LEANN dense + Graphiti KG) is intentionally not
called from this hook. SessionStart has a 500ms p95 budget. We hold
the p95 with fast SQLite reads; the optional git summary runs two
short ``git`` execs (status + log) under a shared
``GIT_BUDGET_SECONDS`` deadline, so a slow or hung repo degrades the
git block rather than blowing the budget by seconds.
"""

from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
import time
from datetime import date
from pathlib import Path
from typing import Any

from mneme_core.cce.budget import estimate_tokens
from mneme_core.cce.checkpoint import WorkingSetItem
from mneme_core.cce.config import read_config
from mneme_core.cce.loss_detect import detect_dropped, load_latest_checkpoint
from mneme_core.injection import wrap_untrusted
from mneme_core.vault.atomic_write import atomic_write_text
from mneme_core.vault.config import VaultConfig

from .lib import emit, run_hook

MAX_CHARS = 8_000
RECENT_SESSION_LIMIT = 5
GIT_STATUS_LINE_LIMIT = 10
# Hard ceiling shared by the two git execs. The healthy-repo p95 is far
# below this; the budget only bites when git is slow or hung, where we
# would rather drop the git block than make every session wait.
GIT_BUDGET_SECONDS = 1.5

# State.json key written by PreCompact and read here to detect compaction.
_STATE_FILENAME = "state.json"
_KEY_LAST_PRECOMPACT = "last_precompact_at"
_KEY_LAST_REHYDRATED = "last_rehydrated_precompact_at"


def _today_iso() -> str:
    return date.today().isoformat()


def _block_date() -> str:
    return f"### Date\n{_today_iso()}\n"


def _block_today_headings(vault: VaultConfig) -> str:
    # The Stop hook writes the daily session log under sessions/ (its
    # LOG_DIR_NAME), so read from there rather than the vault root.
    today_md = vault.root / "sessions" / f"{_today_iso()}.md"
    if not today_md.exists():
        return ""
    try:
        lines = today_md.read_text(encoding="utf-8").splitlines()
    except OSError:
        return ""
    headings = [ln for ln in lines if ln.startswith("##")][-5:]
    if not headings:
        return ""
    out = ["### Today's recent section headings", ""]
    out.extend(f"- {h.lstrip('# ').strip()}" for h in headings)
    return "\n".join(out) + "\n"


def _block_git_summary(vault_root: Path) -> str:
    if not (vault_root / ".git").exists():
        return ""
    deadline = time.monotonic() + GIT_BUDGET_SECONDS
    try:
        status = subprocess.run(
            ["git", "status", "--short"],
            cwd=str(vault_root),
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=GIT_BUDGET_SECONDS,
        )
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            # status alone consumed the budget on a slow repo; drop the
            # whole block rather than starting a second exec over budget.
            return ""
        log = subprocess.run(
            ["git", "log", "--oneline", "-5"],
            cwd=str(vault_root),
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=remaining,
        )
    except (subprocess.TimeoutExpired, OSError, FileNotFoundError):
        return ""

    status_lines = [ln for ln in status.stdout.splitlines() if ln.strip()]
    out = ["### Vault git status"]
    if status_lines:
        head = status_lines[:GIT_STATUS_LINE_LIMIT]
        out.append(f"{len(status_lines)} changed (showing first {len(head)}):")
        out.extend(f"  {ln}" for ln in head)
    else:
        out.append("Clean")
    out.append("")
    out.append("### Last 5 commits")
    out.append(log.stdout.strip() if log.stdout.strip() else "(no git log)")
    return "\n".join(out) + "\n"


def _block_recent_sessions(vault: VaultConfig) -> str:
    if not vault.fts5_db.exists():
        return ""
    try:
        conn = sqlite3.connect(
            f"file:{vault.fts5_db}?mode=ro",
            uri=True,
        )
    except sqlite3.OperationalError:
        # mode=ro raises OperationalError when the file is missing or
        # cannot be opened for reading; degrade gracefully.
        return ""
    except sqlite3.Error:
        return ""
    try:
        rows = conn.execute(
            "SELECT path, COALESCE(title, '') AS title "
            "FROM documents "
            "WHERE frontmatter_type = 'session' "
            "ORDER BY mtime DESC LIMIT ?",
            (RECENT_SESSION_LIMIT,),
        ).fetchall()
    except sqlite3.Error:
        return ""
    finally:
        conn.close()

    if not rows:
        return ""
    out = ["### Most recent session documents", ""]
    for path, title in rows:
        label = title or path
        out.append(f"- `{path}` -- {label}")
    return "\n".join(out) + "\n"


def _is_post_compaction(event: dict[str, Any], vault: VaultConfig) -> bool:
    """Return True when this session start follows a compaction event.

    Two detection mechanisms, evaluated in order:

    1. ``event["source"] == "compact"`` — direct signal from Claude Code.
    2. Fallback: ``state.json["last_precompact_at"]`` is set and differs
       from ``state.json["last_rehydrated_precompact_at"]``, meaning a
       PreCompact stamp exists that has not yet been rehydrated.
    """
    if event.get("source") == "compact":
        return True

    state_path = vault.state_dir / _STATE_FILENAME
    try:
        if not state_path.is_file():
            return False
        state: dict[str, Any] = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False

    last_precompact = state.get(_KEY_LAST_PRECOMPACT)
    if not last_precompact:
        return False
    last_rehydrated = state.get(_KEY_LAST_REHYDRATED)
    return bool(last_precompact != last_rehydrated)


def _mark_rehydrated(vault: VaultConfig, last_precompact_at: str) -> None:
    """Stamp ``last_rehydrated_precompact_at`` into state.json.

    Prevents the self-heal block from firing again for the same compaction.
    Fails silently so the main context preamble is always emitted.
    """
    state_path = vault.state_dir / _STATE_FILENAME
    try:
        if state_path.is_file():
            state: dict[str, Any] = json.loads(state_path.read_text(encoding="utf-8"))
            if not isinstance(state, dict):
                state = {}
        else:
            state = {}
        state[_KEY_LAST_REHYDRATED] = last_precompact_at
        state_path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_text(
            state_path,
            json.dumps(state, indent=2, ensure_ascii=False) + "\n",
            vault_root=vault.root,
        )
    except OSError:
        pass


def _read_last_precompact_at(vault: VaultConfig) -> str:
    """Return the ``last_precompact_at`` value from state.json, or empty string."""
    state_path = vault.state_dir / _STATE_FILENAME
    try:
        if not state_path.is_file():
            return ""
        state = json.loads(state_path.read_text(encoding="utf-8"))
        if not isinstance(state, dict):
            return ""
        val = state.get(_KEY_LAST_PRECOMPACT, "")
        return str(val) if val else ""
    except (OSError, json.JSONDecodeError):
        return ""


def _block_self_heal(
    vault: VaultConfig,
    event: dict[str, Any],
    budget_chars: int,
) -> tuple[str, str]:
    """Build a self-heal rehydration block for post-compaction session starts.

    Returns ``(block_text, last_precompact_at)`` where ``block_text`` is
    the markdown block to append (empty string when nothing to inject) and
    ``last_precompact_at`` is the stamp to record once the block is emitted.
    The block is truncated to fit within *budget_chars*.

    Gated on:
    - CCE enabled in vault config.
    - Compaction detected via ``_is_post_compaction``.
    - A latest checkpoint existing with at least one item.
    - A transcript path available in the event.

    Fails silently and returns ``("", "")`` on any error.
    """
    try:
        cce_config = read_config(vault.cce_config_path)
        if not cce_config.enabled:
            return "", ""

        if not _is_post_compaction(event, vault):
            return "", ""

        last_precompact_at = _read_last_precompact_at(vault)

        checkpoint = load_latest_checkpoint(
            vault.checkpoint_index, scope=vault.default_scope()
        )
        if checkpoint is None or not checkpoint.items:
            return "", last_precompact_at

        transcript_path_str = event.get("transcript_path") or ""
        transcript_path = Path(transcript_path_str) if transcript_path_str else Path("")

        dropped = detect_dropped(checkpoint, transcript_path)
        if not dropped:
            return "", last_precompact_at

        # Greedily select dropped items up to the rehydration token budget.
        selected: list[WorkingSetItem] = []
        tokens_used = 0
        for item in dropped:
            item_tokens = estimate_tokens(item.text)
            if tokens_used + item_tokens > cce_config.rehydration_token_budget:
                break
            selected.append(item)
            tokens_used += item_tokens

        if not selected:
            return "", last_precompact_at

        truncated = len(selected) < len(dropped)
        remaining = len(dropped) - len(selected)

        lines: list[str] = ["### Recovered from compaction", ""]
        for item in selected:
            lines.append(f"- [{item.kind}, salience {item.salience:.2f}] {item.text}")
        if truncated:
            lines.append(
                f"\n({remaining} more checkpoint item{'s' if remaining != 1 else ''}"
                " in the vault, ask mneme to expand)"
            )
        block = "\n".join(lines) + "\n"

        # Truncate to the remaining character budget.
        if len(block) > budget_chars:
            block = block[:budget_chars]

        return block, last_precompact_at
    except Exception:
        return "", ""


def _build_context(vault: VaultConfig, event: dict[str, Any] | None = None) -> str:
    # Vault-derived blocks are untrusted: a crafted note title, section
    # heading, or commit message could carry prompt-injection text that
    # would otherwise be surfaced as authoritative preamble. Fence them
    # with the spotlighting guard (gap G-3) so the model treats them as
    # data. The date block is system-generated and stays outside.
    untrusted = "\n".join(
        b
        for b in (
            _block_today_headings(vault),
            _block_git_summary(vault.root),
            _block_recent_sessions(vault),
        )
        if b
    )
    parts: list[str] = ["## Vault Context (mneme SessionStart)", "", _block_date()]
    if untrusted:
        parts.append(wrap_untrusted(untrusted, source="vault-session-start"))
    full = "\n".join(p for p in parts if p)
    if len(full) > MAX_CHARS:
        full = full[:MAX_CHARS] + "\n\n[CONTEXT TRUNCATED]"
        return full

    # CCE self-heal block: append after existing content, within MAX_CHARS.
    ev = event or {}
    budget_chars = MAX_CHARS - len(full) - 1  # -1 for the joining newline
    if budget_chars > 0:
        heal_block, last_precompact_at = _block_self_heal(vault, ev, budget_chars)
        if heal_block:
            full = full + "\n" + wrap_untrusted(
                heal_block, source="cce-checkpoint-rehydration"
            )
            if len(full) > MAX_CHARS:
                full = full[:MAX_CHARS]
            # Record that this compaction has been rehydrated.
            if last_precompact_at:
                _mark_rehydrated(vault, last_precompact_at)

    return full


def handle(event: dict[str, Any], vault: VaultConfig | None) -> None:
    if vault is None:
        emit(hook_event_name="SessionStart")
        return
    context = _build_context(vault, event)
    emit(hook_event_name="SessionStart", additional_context=context)


def main() -> int:
    return run_hook(handle, hook_event_name="SessionStart")


if __name__ == "__main__":
    sys.exit(main())
