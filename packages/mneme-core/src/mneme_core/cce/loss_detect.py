"""Post-compaction loss detection for the Context Continuity Engine.

The critical path remains deterministic and local. Checkpoint lookup is
scope-aware, bounded, and vault-contained. Corrupt derived index records are
skipped without allowing them to redirect reads outside the user-owned vault.
"""

from __future__ import annotations

import json
from pathlib import Path

from ..scope import DEFAULT_SCOPE, scope_matches, valid_scope
from .checkpoint import Checkpoint, WorkingSetItem, parse_markdown

_MAX_TRANSCRIPT_BYTES = 2 * 1024 * 1024
_MAX_INDEX_BYTES = 16 * 1024 * 1024
_MAX_CHECKPOINT_BYTES = 4 * 1024 * 1024
_KEY_CHARS = 60


def _normalize(text: str) -> str:
    """Lowercase and collapse all whitespace runs to a single space."""
    return " ".join(text.lower().split())


def _item_key(item: WorkingSetItem) -> str:
    """Return the normalized lookup key for *item*."""
    return _normalize(item.text)[:_KEY_CHARS]


def detect_dropped(
    checkpoint: Checkpoint,
    post_transcript_path: Path,
) -> tuple[WorkingSetItem, ...]:
    """Return checkpoint items whose content did not survive compaction."""
    if not checkpoint.items:
        return ()

    transcript_text = _read_transcript_text(post_transcript_path)
    if transcript_text is None:
        return ()

    normalized_transcript = _normalize(transcript_text)
    dropped = [
        item
        for item in checkpoint.items
        if (key := _item_key(item)) and key not in normalized_transcript
    ]
    dropped.sort(key=lambda item: -item.salience)
    return tuple(dropped)


def _vault_root_from_index(index_path: Path) -> Path | None:
    parent = index_path.parent
    if parent.name != "checkpoints" or parent.parent.name != ".mneme":
        return None
    return parent.parent.parent.resolve()


def _contained_checkpoint_path(index_path: Path, value: str) -> Path | None:
    root = _vault_root_from_index(index_path)
    raw = Path(value)
    candidate = raw if raw.is_absolute() else (root / raw if root is not None else raw)
    try:
        resolved = candidate.resolve(strict=True)
    except OSError:
        return None
    if root is not None and not resolved.is_relative_to(root):
        return None
    return resolved if resolved.is_file() else None


def _read_bounded(path: Path, limit: int) -> str | None:
    try:
        if path.stat().st_size > limit:
            return None
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return None


def load_latest_checkpoint(
    vault_checkpoint_index: Path,
    scope: str = DEFAULT_SCOPE,
) -> Checkpoint | None:
    """Load the newest checkpoint visible in ``scope``.

    Legacy records without scope metadata belong only to ``default``. The
    exact ``*`` selector is accepted only when the caller supplies it
    explicitly. Index and checkpoint reads are bounded, relative paths resolve
    against the vault root, absolute legacy paths remain supported only when
    they stay inside that root, and malformed records are skipped.
    """
    requested_scope = valid_scope(scope)
    if requested_scope is None:
        return None
    if not vault_checkpoint_index.is_file():
        return None

    raw = _read_bounded(vault_checkpoint_index, _MAX_INDEX_BYTES)
    if raw is None:
        return None

    for line in reversed(raw.splitlines()):
        stripped = line.strip()
        if not stripped:
            continue
        try:
            parsed = json.loads(stripped)
        except json.JSONDecodeError:
            continue
        if not isinstance(parsed, dict):
            continue
        if not scope_matches(parsed.get("scope"), requested_scope):
            continue
        path_value = parsed.get("path")
        if not isinstance(path_value, str) or not path_value:
            continue
        doc_path = _contained_checkpoint_path(vault_checkpoint_index, path_value)
        if doc_path is None:
            continue
        markdown = _read_bounded(doc_path, _MAX_CHECKPOINT_BYTES)
        if markdown is None:
            continue
        try:
            checkpoint = parse_markdown(markdown)
        except ValueError:
            continue
        if not scope_matches(checkpoint.scope, requested_scope):
            continue
        record_anchor = parsed.get("anchor")
        if isinstance(record_anchor, str) and record_anchor != checkpoint.anchor:
            continue
        return checkpoint
    return None


def _read_transcript_text(path: Path) -> str | None:
    """Read message text from a bounded JSONL transcript."""
    try:
        if not path.is_file():
            return None
        blob = path.read_bytes()[:_MAX_TRANSCRIPT_BYTES]
    except OSError:
        return None

    parts: list[str] = []
    for raw_line in blob.decode("utf-8", errors="replace").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(record, dict):
            continue
        message = record.get("message")
        content: object = None
        if isinstance(message, dict):
            content = message.get("content")
        elif "content" in record:
            content = record["content"]

        if isinstance(content, str):
            parts.append(content)
        elif isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and block.get("type") == "text":
                    text = block.get("text")
                    if isinstance(text, str):
                        parts.append(text)

    return " ".join(parts) if parts else None
