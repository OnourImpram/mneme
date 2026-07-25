"""Post-compaction loss detection for CCE Phase 3.

``detect_dropped`` scans a post-compaction transcript for each item from the
latest checkpoint and returns those whose content did not survive compaction.
The result is sorted by salience descending so callers can greedily pick the
most important items first when building a rehydration block.

Design constraints honored here:

* Zero-LLM.  All matching is deterministic string search.
* Fail-soft.  A missing or corrupt transcript returns an empty tuple.
* Pure.  No filesystem writes, no side effects beyond reading.
* ``load_latest_checkpoint`` is a convenience helper that reads the last line
  of the checkpoint index and parses the referenced markdown file.  It mirrors
  the lookup that ``build.py`` performs for ``prev_anchor`` but is factored
  here so Phase 3 callers do not need to import from ``build``.
"""

from __future__ import annotations

import json
import os
import stat
from pathlib import Path

from ..scope import DEFAULT_SCOPE, scope_matches, valid_scope
from .checkpoint import Checkpoint, WorkingSetItem, parse_markdown

# Mirror the cap used by budget.py and session_summary so we never block
# on a huge post-compaction transcript.
_MAX_TRANSCRIPT_BYTES = 2 * 1024 * 1024
_MAX_INDEX_BYTES = 16 * 1024 * 1024
_MAX_CHECKPOINT_BYTES = 4 * 1024 * 1024

# Number of characters from the start of each item's text used as the
# lookup key.  Short enough to survive minor whitespace normalisation,
# long enough to avoid false-positive collisions.
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
    """Return checkpoint items whose content did not survive compaction.

    Reads up to ``_MAX_TRANSCRIPT_BYTES`` of *post_transcript_path* and
    checks each item in *checkpoint* against the full normalized transcript
    text.  An item is considered "dropped" when its key (first
    ``_KEY_CHARS`` chars of its normalized text) is absent from the
    normalized transcript body.

    Args:
        checkpoint: the most recent checkpoint to inspect.
        post_transcript_path: path to the post-compaction JSONL transcript.

    Returns:
        Tuple of dropped :class:`~mneme_core.cce.checkpoint.WorkingSetItem`
        instances, sorted by ``salience`` descending.  Returns ``()`` when
        the transcript is missing, unreadable, or all items survived.
    """
    if not checkpoint.items:
        return ()

    transcript_text = _read_transcript_text(post_transcript_path)
    if transcript_text is None:
        return ()

    normalized_transcript = _normalize(transcript_text)

    dropped: list[WorkingSetItem] = []
    for item in checkpoint.items:
        key = _item_key(item)
        if not key:
            continue
        if key not in normalized_transcript:
            dropped.append(item)

    dropped.sort(key=lambda i: -i.salience)
    return tuple(dropped)


def _vault_root_from_index(index_path: Path) -> Path | None:
    parent = index_path.parent
    if parent.name != "checkpoints" or parent.parent.name != ".mneme":
        return None
    try:
        return parent.parent.parent.resolve(strict=True)
    except OSError:
        return None


def _read_bounded_regular_file(path: Path, limit: int) -> str | None:
    """Read a bounded regular file without following its final symlink."""
    nofollow = getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(path, os.O_RDONLY | nofollow)
    except OSError:
        return None
    try:
        file_stat = os.fstat(fd)
        if not stat.S_ISREG(file_stat.st_mode) or file_stat.st_size > limit:
            return None
        chunks: list[bytes] = []
        remaining = limit + 1
        while remaining > 0:
            chunk = os.read(fd, min(1024 * 1024, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        payload = b"".join(chunks)
        if len(payload) > limit:
            return None
        return payload.decode("utf-8")
    except (OSError, UnicodeDecodeError):
        return None
    finally:
        os.close(fd)


def _contained_checkpoint_path(root: Path, value: str) -> Path | None:
    raw = Path(value)
    candidate = raw if raw.is_absolute() else root / raw
    try:
        resolved = candidate.resolve(strict=True)
    except OSError:
        return None
    if not resolved.is_relative_to(root) or not resolved.is_file():
        return None
    return resolved


def load_latest_checkpoint(
    vault_checkpoint_index: Path,
    scope: str = DEFAULT_SCOPE,
) -> Checkpoint | None:
    """Load the newest checkpoint visible in a validated read scope.

    Legacy records without scope metadata belong only to ``default``. The
    exact ``*`` selector is accepted only when supplied explicitly. Index and
    checkpoint reads are bounded, vault-contained, and symlink safe.
    """
    requested_scope = valid_scope(scope)
    if requested_scope is None:
        return None
    root = _vault_root_from_index(vault_checkpoint_index)
    if root is None:
        return None
    try:
        resolved_index = vault_checkpoint_index.resolve(strict=True)
    except OSError:
        return None
    if not resolved_index.is_relative_to(root):
        return None
    raw = _read_bounded_regular_file(resolved_index, _MAX_INDEX_BYTES)
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
        doc_path = _contained_checkpoint_path(root, path_value)
        if doc_path is None:
            continue
        markdown = _read_bounded_regular_file(doc_path, _MAX_CHECKPOINT_BYTES)
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


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------


def _read_transcript_text(path: Path) -> str | None:
    """Read and concatenate all message text from a JSONL transcript.

    Returns the concatenated plain text extracted from every message record,
    or ``None`` when the file is missing or entirely unreadable.  Individual
    corrupt lines are skipped silently.
    """
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
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(rec, dict):
            continue
        # Extract text from message.content blocks (same shape as budget.py).
        message = rec.get("message")
        content: object = None
        if isinstance(message, dict):
            content = message.get("content")
        elif "content" in rec:
            content = rec["content"]

        if isinstance(content, str):
            parts.append(content)
        elif isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and block.get("type") == "text":
                    text = block.get("text")
                    if isinstance(text, str):
                        parts.append(text)

    if not parts:
        return None
    return " ".join(parts)
