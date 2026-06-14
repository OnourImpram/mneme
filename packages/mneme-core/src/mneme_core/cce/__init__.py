"""Context Continuity Engine (CCE) — Phase 1 substrate.

Public surface exposed here is intentionally minimal: consumers import
directly from the sub-modules for everything beyond the top-level names.
"""

from __future__ import annotations

from .checkpoint import (
    Checkpoint,
    WorkingSetItem,
    append_index,
    delta,
    make_anchor,
    parse_markdown,
    render_markdown,
)
from .config import CceConfig, read_config, write_config
from .salience import score_item

__all__ = [
    "CceConfig",
    "read_config",
    "write_config",
    "Checkpoint",
    "WorkingSetItem",
    "make_anchor",
    "render_markdown",
    "parse_markdown",
    "delta",
    "append_index",
    "score_item",
]
