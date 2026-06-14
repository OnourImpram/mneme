"""Checkpoint data model and persistence helpers for CCE Phase 1.

A checkpoint is a point-in-time snapshot of the session working set —
the items that matter most for continuing work after a context boundary.
Checkpoints are stored as markdown documents (git-visible, human-readable)
with YAML frontmatter and indexed by a JSONL sidecar for fast lookup.

Privacy: all text content is passed through :func:`mneme_core.privacy.redact`
before being rendered to disk, honoring constraint C4.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..privacy import redact
from ..vault.atomic_write import atomic_write_text
from ..vault.file_lock import file_lock

_LOCK_TIMEOUT_S = 5.0


@dataclass(frozen=True)
class WorkingSetItem:
    """A single item in the checkpoint working set."""

    kind: str
    text: str
    salience: float
    refs: tuple[str, ...] = ()


@dataclass(frozen=True)
class Checkpoint:
    """Immutable point-in-time snapshot of a session working set."""

    anchor: str
    created: str
    session_id: str
    prev_anchor: str | None
    items: tuple[WorkingSetItem, ...]
    schema_version: int = 1


def make_anchor(created: str, session_id: str) -> str:
    """First 12 hex chars of SHA-256 over created + session_id.

    Deterministic: same inputs always produce the same anchor.
    """
    digest = hashlib.sha256((created + session_id).encode()).hexdigest()
    return digest[:12]


def render_markdown(cp: Checkpoint) -> str:
    """Render a Checkpoint to a markdown string with YAML frontmatter.

    All text fields are passed through :func:`mneme_core.privacy.redact`
    before rendering so ``<private>`` spans never survive to disk.
    """
    prev = cp.prev_anchor if cp.prev_anchor is not None else "null"
    lines: list[str] = [
        "---",
        f"id: checkpoint-{cp.anchor}",
        "type: checkpoint",
        f"created: {cp.created}",
        f"session_id: {cp.session_id}",
        f"anchor: {cp.anchor}",
        f"prev_anchor: {prev}",
        f"schema_version: {cp.schema_version}",
        "---",
        "",
        f"# Checkpoint {cp.anchor}",
        "",
    ]

    # Group items by kind, preserving insertion order of first appearance.
    sections: dict[str, list[WorkingSetItem]] = {}
    for item in cp.items:
        sections.setdefault(item.kind, []).append(item)

    for kind, kind_items in sections.items():
        lines.append(f"## {kind}")
        lines.append("")
        for item in kind_items:
            safe_text = redact(item.text)
            bullet = f"- [salience {item.salience:.2f}] {safe_text}"
            if item.refs:
                refs_str = " ".join(f"`{redact(r)}`" for r in item.refs)
                bullet = f"{bullet} {refs_str}"
            lines.append(bullet)
        lines.append("")

    return "\n".join(lines)


def parse_markdown(text: str) -> Checkpoint:
    """Parse a rendered checkpoint markdown back into a Checkpoint.

    Tolerant of missing sections; round-trips cleanly with render_markdown.
    """
    anchor = ""
    created = ""
    session_id = ""
    prev_anchor: str | None = None
    schema_version = 1

    in_frontmatter = False
    frontmatter_done = False
    current_kind: str | None = None
    items: list[WorkingSetItem] = []

    for line in text.splitlines():
        # --- frontmatter parsing ---
        if not frontmatter_done:
            if line.strip() == "---":
                if not in_frontmatter:
                    in_frontmatter = True
                    continue
                else:
                    frontmatter_done = True
                    continue
            if in_frontmatter and ":" in line:
                key, _, val = line.partition(":")
                key = key.strip()
                val = val.strip()
                if key == "anchor":
                    anchor = val
                elif key == "created":
                    created = val
                elif key == "session_id":
                    session_id = val
                elif key == "prev_anchor":
                    prev_anchor = None if val in ("null", "~", "") else val
                elif key == "schema_version":
                    try:
                        schema_version = int(val)
                    except ValueError:
                        schema_version = 1
            continue

        # --- body parsing ---
        if line.startswith("## "):
            current_kind = line[3:].strip()
            continue

        if current_kind is not None and line.startswith("- [salience "):
            # Format: - [salience X.XX] <text> [optional `ref` `ref`]
            rest = line[len("- [salience "):]
            sal_end = rest.find("]")
            if sal_end == -1:
                continue
            try:
                salience = float(rest[:sal_end])
            except ValueError:
                salience = 0.0
            remainder = rest[sal_end + 2:]  # skip "] "

            # Split off trailing backtick refs
            refs: list[str] = []
            parts = remainder.split(" `")
            text_part = parts[0]
            for ref_part in parts[1:]:
                ref_val = ref_part.rstrip("`").rstrip()
                if ref_val:
                    refs.append(ref_val)

            items.append(
                WorkingSetItem(
                    kind=current_kind,
                    text=text_part,
                    salience=salience,
                    refs=tuple(refs),
                )
            )

    return Checkpoint(
        anchor=anchor,
        created=created,
        session_id=session_id,
        prev_anchor=prev_anchor,
        items=tuple(items),
        schema_version=schema_version,
    )


def delta(
    prev: Checkpoint | None,
    curr: Checkpoint,
) -> tuple[WorkingSetItem, ...]:
    """Items in curr whose (kind, text) pair is not present in prev."""
    if prev is None:
        return curr.items
    existing: set[tuple[str, str]] = {(i.kind, i.text) for i in prev.items}
    return tuple(item for item in curr.items if (item.kind, item.text) not in existing)


def append_index(
    index_path: Path,
    cp: Checkpoint,
    doc_path: Path,
) -> None:
    """Append one JSON line to the checkpoint index.

    The record captures enough metadata for fast lookup without reading the
    full markdown document. Uses the same locked-append pattern as the
    compression cost ledger.

    Args:
        index_path: path to the JSONL index file.
        cp: checkpoint to index.
        doc_path: path to the markdown document on disk.
    """
    top_salience = max((i.salience for i in cp.items), default=0.0)
    record: dict[str, Any] = {
        "anchor": cp.anchor,
        "id": f"checkpoint-{cp.anchor}",
        "created": cp.created,
        "session_id": cp.session_id,
        "prev_anchor": cp.prev_anchor,
        "path": str(doc_path),
        "item_count": len(cp.items),
        "top_salience": round(top_salience, 4),
    }
    lock_path = index_path.with_suffix(index_path.suffix + ".lock")
    with file_lock(lock_path, timeout_s=_LOCK_TIMEOUT_S):
        index_path.parent.mkdir(parents=True, exist_ok=True)
        with index_path.open("a", encoding="utf-8") as fp:
            fp.write(json.dumps(record, ensure_ascii=False) + "\n")


def _save(cp: Checkpoint, doc_path: Path) -> None:
    """Write a checkpoint markdown document atomically."""
    atomic_write_text(doc_path, render_markdown(cp))
