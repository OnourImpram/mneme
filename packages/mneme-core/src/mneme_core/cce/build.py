"""Zero-LLM working-set extraction and checkpoint persistence for CCE Phase 2.

``build_checkpoint`` extracts a deterministic WorkingSet from:
  - Files touched this session (from staging event tool_input paths).
  - Recent decisions, TODOs, and intent (from transcript head).
  - A synthetic "next_action" item derived from the last user prompt.

All text is passed through ``privacy.redact`` before being stored in
the Checkpoint dataclass. Salience scores are computed deterministically
via ``salience.score_item`` using the operator-configured weights.

``write_checkpoint`` renders the checkpoint to markdown, writes it
atomically under ``vault.checkpoints_dir``, appends a line to
``vault.checkpoint_index``, and prunes oldest files if ``max_checkpoints``
is exceeded.
"""

from __future__ import annotations

import contextlib
import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ..distill.session_summary import (
    _MAX_TRANSCRIPT_BYTES,
    _collect_stats,
    _extract_text,
    _iter_session_records,
)
from ..privacy import redact
from ..vault.atomic_write import atomic_write_text
from ..vault.config import VaultConfig
from .checkpoint import (
    Checkpoint,
    WorkingSetItem,
    append_index,
    make_anchor,
    render_markdown,
)
from .config import CceConfig, read_config
from .salience import score_item

# Maximum number of files to include in the working set.
_MAX_FILES = 12
# Maximum characters of transcript head to read for intent/decisions.
_MAX_INTENT_CHARS = 240
# Simple heuristics for decision / TODO extraction from transcript text.
_DECISION_RE = re.compile(
    r"\b(decided?|decision|conclusion|agreed?|resolved?|will use|chosen?)\b",
    re.IGNORECASE,
)
_TODO_RE = re.compile(
    r"\b(todo|TODO|FIXME|next step|action item|hatirlatma|yapilacak)\b",
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def build_checkpoint(
    vault: VaultConfig,
    session_id: str,
    transcript_path: str,
    prev_anchor: str | None,
) -> Checkpoint:
    """Build a Checkpoint from deterministic working-set extraction.

    Args:
        vault: resolved VaultConfig for the current vault.
        session_id: the Claude Code session identifier.
        transcript_path: path to the JSONL transcript for this session.
        prev_anchor: anchor string of the previous checkpoint, or None.

    Returns:
        A fully populated, privacy-redacted Checkpoint ready for writing.
    """
    config = read_config(vault.cce_config_path)
    created = datetime.now(UTC).isoformat()
    anchor = make_anchor(created, session_id)

    items: list[WorkingSetItem] = []

    # --- Files touched (from staging) ---
    records = _iter_session_records(vault, session_id)
    stats = _collect_stats(records)
    for file_path in stats.files_touched[:_MAX_FILES]:
        safe_path = redact(file_path)
        sal = score_item(
            "recent_edit",
            {"recent_edit": 1.0},
            config.salience_weights,
        )
        items.append(
            WorkingSetItem(
                kind="recent_edit",
                text=safe_path,
                salience=sal,
                refs=(),
            )
        )

    # --- Decisions, TODOs, and intent from transcript head ---
    transcript_items = _extract_transcript_items(transcript_path, config)
    items.extend(transcript_items)

    # Sort by salience descending so the most important items come first.
    items.sort(key=lambda i: -i.salience)

    return Checkpoint(
        anchor=anchor,
        created=created,
        session_id=session_id,
        prev_anchor=prev_anchor,
        items=tuple(items),
    )


def write_checkpoint(vault: VaultConfig, cp: Checkpoint) -> Path:
    """Render and persist a checkpoint, then prune if over the cap.

    Args:
        vault: resolved VaultConfig.
        cp: checkpoint to write.

    Returns:
        Path of the written markdown file.
    """
    config = read_config(vault.cce_config_path)
    date_str = cp.created[:10]  # YYYY-MM-DD
    filename = f"{date_str}-{cp.anchor}.md"
    doc_path = vault.checkpoints_dir / filename

    vault.checkpoints_dir.mkdir(parents=True, exist_ok=True)
    atomic_write_text(doc_path, render_markdown(cp))
    append_index(vault.checkpoint_index, cp, doc_path)

    _prune_checkpoints(vault, config.max_checkpoints)
    return doc_path


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------


def _extract_transcript_items(
    transcript_path: str,
    config: CceConfig,
) -> list[WorkingSetItem]:
    """Extract decision, todo, and intent items from the transcript head."""
    items: list[WorkingSetItem] = []
    if not transcript_path:
        return items

    path = Path(transcript_path)
    try:
        if not path.is_file():
            return items
        blob = path.read_bytes()[:_MAX_TRANSCRIPT_BYTES]
    except OSError:
        return items

    seen_texts: set[str] = set()
    first_user_text: str = ""

    for raw_line in blob.decode("utf-8", errors="replace").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(rec, dict):
            continue

        message = rec.get("message")
        role = ""
        content: object = None
        if isinstance(message, dict):
            role = str(message.get("role") or "")
            content = message.get("content")
        if role != "user" and str(rec.get("type") or "") != "user":
            continue

        text = _extract_text(content).strip()
        if not text:
            continue
        text = redact(text)

        # Capture the first user message as "intent".
        if not first_user_text:
            first_user_text = text[:_MAX_INTENT_CHARS]
            if len(text) > _MAX_INTENT_CHARS:
                first_user_text = first_user_text[: _MAX_INTENT_CHARS - 1] + "…"

        # Scan for decision/TODO signals in each sentence.
        for sentence in re.split(r"[.\n!?]", text):
            sentence = sentence.strip()
            if not sentence or sentence in seen_texts:
                continue
            if _DECISION_RE.search(sentence):
                safe = redact(sentence[:_MAX_INTENT_CHARS])
                if safe not in seen_texts:
                    seen_texts.add(safe)
                    sal = score_item(
                        "decision",
                        {"decision": 1.0},
                        config.salience_weights,
                    )
                    items.append(
                        WorkingSetItem(kind="decision", text=safe, salience=sal)
                    )
            elif _TODO_RE.search(sentence):
                safe = redact(sentence[:_MAX_INTENT_CHARS])
                if safe not in seen_texts:
                    seen_texts.add(safe)
                    sal = score_item(
                        "todo",
                        {"todo": 1.0},
                        config.salience_weights,
                    )
                    items.append(
                        WorkingSetItem(kind="todo", text=safe, salience=sal)
                    )

    # Add the intent item last (lowest salience category).
    if first_user_text and first_user_text not in seen_texts:
        sal = score_item("recent_file", {"recent_file": 1.0}, config.salience_weights)
        items.append(
            WorkingSetItem(kind="intent", text=first_user_text, salience=sal)
        )

    return items


def _prune_checkpoints(vault: VaultConfig, max_checkpoints: int) -> None:
    """Remove oldest checkpoint markdown files if count exceeds the cap.

    Rewrites the index by removing pruned entries rather than rebuilding
    from disk scans, keeping the operation O(n) on index lines.
    """
    if not vault.checkpoints_dir.is_dir():
        return

    md_files: list[Path] = sorted(
        vault.checkpoints_dir.glob("*.md"),
        key=lambda p: p.stat().st_mtime,
    )
    if len(md_files) <= max_checkpoints:
        return

    to_remove = set(md_files[: len(md_files) - max_checkpoints])
    for old_file in to_remove:
        with contextlib.suppress(OSError):
            old_file.unlink(missing_ok=True)

    # Rewrite the index, dropping lines whose path was removed.
    index_path = vault.checkpoint_index
    if not index_path.is_file():
        return
    try:
        lines = index_path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return
    kept: list[str] = []
    removed_paths = {str(p) for p in to_remove}
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            rec: dict[str, Any] = json.loads(line)
        except json.JSONDecodeError:
            kept.append(line)
            continue
        if rec.get("path") not in removed_paths:
            kept.append(line)
    with contextlib.suppress(OSError):
        atomic_write_text(index_path, "\n".join(kept) + ("\n" if kept else ""))
