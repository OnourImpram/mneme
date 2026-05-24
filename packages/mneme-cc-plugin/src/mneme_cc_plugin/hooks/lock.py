"""Cross-platform exclusive file lock for hook concurrency control.

The canonical implementation moved to
``mneme_core.vault.file_lock`` so it can be reused by the
compression cost ledger (Codex Pass 2 reservation pattern). This
module re-exports the same symbol so existing hook imports do not
break.
"""

from __future__ import annotations

from mneme_core.vault.file_lock import file_lock

__all__ = ["file_lock"]
