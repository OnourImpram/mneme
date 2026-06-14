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
from pathlib import Path

from .checkpoint import Checkpoint, WorkingSetItem, parse_markdown

# Mirror the cap used by budget.py and session_summary so we never block
# on a huge post-compaction transcript.
_MAX_TRANSCRIPT_BYTES = 2 * 1024 * 1024

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


def load_latest_checkpoint(vault_checkpoint_index: Path) -> Checkpoint | None:
    """Load and parse the most recent checkpoint from the index.

    Reads the *last non-empty line* of the JSONL index file, resolves the
    ``path`` field to the markdown document, and returns the parsed
    :class:`~mneme_core.cce.checkpoint.Checkpoint`.

    Returns ``None`` when the index is missing, empty, corrupt, or the
    markdown file cannot be read.

    Args:
        vault_checkpoint_index: path to the CCE JSONL index
            (``VaultConfig.checkpoint_index``).
    """
    if not vault_checkpoint_index.is_file():
        return None

    try:
        raw = vault_checkpoint_index.read_text(encoding="utf-8")
    except OSError:
        return None

    last_line = ""
    for line in raw.splitlines():
        stripped = line.strip()
        if stripped:
            last_line = stripped

    if not last_line:
        return None

    try:
        record: dict[str, object] = json.loads(last_line)
    except json.JSONDecodeError:
        return None

    doc_path_str = record.get("path")
    if not isinstance(doc_path_str, str) or not doc_path_str:
        return None

    doc_path = Path(doc_path_str)
    try:
        md_text = doc_path.read_text(encoding="utf-8")
    except OSError:
        return None

    try:
        return parse_markdown(md_text)
    except Exception:  # noqa: BLE001
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
