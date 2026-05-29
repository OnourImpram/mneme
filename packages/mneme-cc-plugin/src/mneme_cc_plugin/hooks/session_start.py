"""SessionStart hook: inject preflight vault context.

Returns up to ``MAX_CHARS`` characters of markdown that Claude Code
surfaces as conversation preamble. The bundle contains:

  - Today's date.
  - The last few headings from any session document modified today.
  - A short git status summary if the vault is a git repository.
  - The five most recently modified session-typed documents.

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

import sqlite3
import subprocess
import sys
import time
from datetime import date
from pathlib import Path
from typing import Any

from mneme_core.injection import wrap_untrusted
from mneme_core.vault.config import VaultConfig

from .lib import emit, run_hook

MAX_CHARS = 8_000
RECENT_SESSION_LIMIT = 5
GIT_STATUS_LINE_LIMIT = 10
# Hard ceiling shared by the two git execs. The healthy-repo p95 is far
# below this; the budget only bites when git is slow or hung, where we
# would rather drop the git block than make every session wait.
GIT_BUDGET_SECONDS = 1.5


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


def _build_context(vault: VaultConfig) -> str:
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


def handle(event: dict[str, Any], vault: VaultConfig | None) -> None:
    if vault is None:
        emit(hook_event_name="SessionStart")
        return
    context = _build_context(vault)
    emit(hook_event_name="SessionStart", additional_context=context)


def main() -> int:
    return run_hook(handle, hook_event_name="SessionStart")


if __name__ == "__main__":
    sys.exit(main())
