"""Per-session injection deduplication.

The same vault doc can match many retrievals in one session. Without
deduplication the SessionStart and UserPromptSubmit hooks re-inject
the same content over and over, paying the token cost each time.

``InjectionTracker`` is a tiny per-session set of content hashes the
hook layer has already injected. Hooks consult it before pushing a
candidate doc into context, and mark it after.

State is persisted as a JSON file under
``vault/.mneme/injection-tracker/{session_id}.json`` so trackers
survive process restarts within a session. Old tracker files are
cleaned up by ``mneme-audit`` periodically; the data is cheap to
recompute so cleanup is best-effort.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

TRACKER_SUBDIR = "injection-tracker"


@dataclass
class InjectionTracker:
    """One-session hash set with metadata for the audit CLI."""

    session_id: str
    seen_hashes: set[str] = field(default_factory=set)
    hits: int = 0
    skips: int = 0


def has_injected(tracker: InjectionTracker, content_hash: str) -> bool:
    """Return True when this hash has already been injected this session."""
    return content_hash in tracker.seen_hashes


def mark_injected(tracker: InjectionTracker, content_hash: str) -> None:
    """Record an injection. Idempotent."""
    if content_hash in tracker.seen_hashes:
        tracker.skips += 1
        return
    tracker.seen_hashes.add(content_hash)
    tracker.hits += 1


def _tracker_path(state_dir: Path, session_id: str) -> Path:
    safe_id = "".join(c for c in session_id if c.isalnum() or c in {"-", "_"})
    if not safe_id:
        safe_id = "unknown"
    return state_dir / TRACKER_SUBDIR / f"{safe_id}.json"


def load_tracker(state_dir: Path, session_id: str) -> InjectionTracker:
    """Load tracker state if present, else return a fresh empty tracker."""
    path = _tracker_path(state_dir, session_id)
    if not path.is_file():
        return InjectionTracker(session_id=session_id)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return InjectionTracker(session_id=session_id)
    if not isinstance(raw, dict):
        return InjectionTracker(session_id=session_id)
    return InjectionTracker(
        session_id=str(raw.get("session_id", session_id)),
        seen_hashes=set(raw.get("seen_hashes", []) or []),
        hits=int(raw.get("hits", 0) or 0),
        skips=int(raw.get("skips", 0) or 0),
    )


def save_tracker(state_dir: Path, tracker: InjectionTracker) -> bool:
    """Persist tracker to JSON. Returns True on write, False on disk error."""
    try:
        path = _tracker_path(state_dir, tracker.session_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "session_id": tracker.session_id,
            "seen_hashes": sorted(tracker.seen_hashes),
            "hits": tracker.hits,
            "skips": tracker.skips,
        }
        path.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        return True
    except OSError:
        return False
