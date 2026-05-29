"""Session trajectory recorder.

A trajectory is the sequenced record of what the agent did during a
session: each step is one action plus its short observation. Captured
trajectories let the operator (and future sessions) recall not only
the final state of the vault but the path that led there.

Storage layout: ``vault/trajectories/{YYYY-MM-DD}/{session_id}.md``.
Each session gets one markdown file with frontmatter and a stream of
``### HH:MM:SS step`` headings. Append-only during the session,
sealed by ``end_trajectory``.

Public API:

* ``start_trajectory`` creates the file with frontmatter; idempotent.
* ``record_step`` appends one action/observation pair.
* ``end_trajectory`` stamps ``ended_at`` on the frontmatter.
* ``load_trajectory`` reads a sealed or in-progress trajectory.
* ``list_trajectories`` enumerates trajectories by date range.

All functions return structured outcomes; none of them raise on
disk error or malformed input.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import yaml

from .privacy import redact as _privacy_redact
from .vault.atomic_write import atomic_write_text
from .vault.file_lock import file_lock
from .vault.frontmatter import load_yaml_block as _load_yaml_block

TRAJECTORY_TYPE = "trajectory"
TRAJECTORY_SCHEMA_VERSION = "1.0.0"


@dataclass
class TrajectoryStep:
    ts: str
    action: str
    input_summary: str = ""
    observation: str = ""


@dataclass
class Trajectory:
    session_id: str
    started_at: str
    ended_at: str | None = None
    steps: list[TrajectoryStep] = field(default_factory=list)
    schema_version: str = TRAJECTORY_SCHEMA_VERSION


def _now() -> datetime:
    return datetime.now(UTC)


def _safe_id(session_id: str) -> str:
    s = re.sub(r"[^A-Za-z0-9._-]+", "-", session_id.strip())
    return (s.strip("-._") or "unknown").lower()


def _trajectory_path(vault: Any, session_id: str, *, on_date: date | None = None) -> Path:
    d = on_date or _now().date()
    return Path(vault.trajectories_dir / d.isoformat() / f"{_safe_id(session_id)}.md")


def _frontmatter_text(traj: Trajectory) -> str:
    fm: dict[str, Any] = {
        "type": TRAJECTORY_TYPE,
        "session_id": traj.session_id,
        "started_at": traj.started_at,
        "ended_at": traj.ended_at,
        "schema_version": traj.schema_version,
    }
    return yaml.safe_dump(fm, sort_keys=False, allow_unicode=True).strip()


def _seal_section_text(traj: Trajectory) -> str:
    return f"---\n{_frontmatter_text(traj)}\n---\n\n# Trajectory\n"


def start_trajectory(vault: Any, session_id: str) -> Path | None:
    """Create the trajectory file. No-op if one already exists."""
    try:
        path = _trajectory_path(vault, session_id)
        if path.is_file():
            return path
        traj = Trajectory(
            session_id=session_id,
            started_at=_now().isoformat(),
            ended_at=None,
            steps=[],
        )
        path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_text(path, _seal_section_text(traj))
        return path
    except OSError:
        return None


def record_step(
    vault: Any,
    session_id: str,
    *,
    action: str,
    input_summary: str = "",
    observation: str = "",
) -> bool:
    """Append one step to the trajectory file. Creates the file if needed.

    Redacts ``<private>`` content from action, input_summary, and
    observation before writing. The read-modify-write sequence is
    protected by an exclusive per-file lock to prevent lost updates
    under concurrent writes from two processes.
    """
    try:
        # Redact user-controlled fields before any disk access.
        action = _privacy_redact(action)
        input_summary = _privacy_redact(input_summary)
        observation = _privacy_redact(observation)

        path = _trajectory_path(vault, session_id)
        lock_path = path.with_suffix(".lock")
        # Hold the lock across start_trajectory + read + write so that
        # concurrent callers cannot interleave their read-modify-write
        # cycles and lose each other's updates.
        with file_lock(lock_path):
            if start_trajectory(vault, session_id) is None:
                return False
            if not path.is_file():
                return False
            existing = path.read_text(encoding="utf-8")
            ts = _now().strftime("%H:%M:%S")
            block_parts = [f"### {ts} - {action}"]
            if input_summary.strip():
                block_parts.append(f"\ninput: {input_summary.strip()}")
            if observation.strip():
                block_parts.append(f"\nobservation: {observation.strip()}")
            block = "\n".join(block_parts) + "\n"
            new_text = existing.rstrip() + "\n\n" + block
            atomic_write_text(path, new_text)
        return True
    except OSError:
        return False


def end_trajectory(vault: Any, session_id: str) -> bool:
    """Seal the trajectory by stamping ``ended_at`` in the frontmatter.

    The read-modify-write sequence is protected by an exclusive per-file
    lock to prevent lost updates under concurrent calls.
    """
    try:
        path = _trajectory_path(vault, session_id)
        if not path.is_file():
            return False
        lock_path = path.with_suffix(".lock")
        with file_lock(lock_path):
            traj = _load_from_path(path)
            if traj is None:
                return False
            traj.ended_at = _now().isoformat()
            # Reserialize: keep body, replace frontmatter.
            text = path.read_text(encoding="utf-8")
            match = _FRONTMATTER_RE.match(text)
            if match is None:
                return False
            body = match.group(2)
            new_text = f"---\n{_frontmatter_text(traj)}\n---\n{body}"
            atomic_write_text(path, new_text)
        return True
    except OSError:
        return False


_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n(.*)$", re.DOTALL)
_STEP_RE = re.compile(
    r"^###\s+(\d{2}:\d{2}:\d{2})\s+-\s+(.+?)\s*$",
    re.MULTILINE,
)


def _load_from_path(path: Path) -> Trajectory | None:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    match = _FRONTMATTER_RE.match(text)
    if match is None:
        return None
    try:
        fm = _load_yaml_block(match.group(1)) or {}
    except (yaml.YAMLError, ValueError):
        return None
    if not isinstance(fm, dict) or fm.get("type") != TRAJECTORY_TYPE:
        return None
    body = match.group(2)
    return Trajectory(
        session_id=str(fm.get("session_id") or ""),
        started_at=str(fm.get("started_at") or ""),
        ended_at=fm.get("ended_at"),
        steps=_parse_steps(body),
        schema_version=str(fm.get("schema_version") or TRAJECTORY_SCHEMA_VERSION),
    )


def _parse_steps(body: str) -> list[TrajectoryStep]:
    matches = list(_STEP_RE.finditer(body))
    steps: list[TrajectoryStep] = []
    for i, m in enumerate(matches):
        ts = m.group(1)
        action = m.group(2)
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(body)
        block = body[start:end]
        input_summary = _grab_line(block, "input:")
        observation = _grab_line(block, "observation:")
        steps.append(
            TrajectoryStep(
                ts=ts,
                action=action,
                input_summary=input_summary,
                observation=observation,
            )
        )
    return steps


def _grab_line(block: str, prefix: str) -> str:
    for line in block.splitlines():
        stripped = line.strip()
        if stripped.startswith(prefix):
            return stripped[len(prefix):].strip()
    return ""


def load_trajectory(vault: Any, session_id: str) -> Trajectory | None:
    """Load a trajectory by session_id. Tries today's date first, then history."""
    today_path = _trajectory_path(vault, session_id)
    if today_path.is_file():
        return _load_from_path(today_path)
    if not vault.trajectories_dir.is_dir():
        return None
    safe = _safe_id(session_id)
    for date_dir in sorted(vault.trajectories_dir.iterdir(), reverse=True):
        if not date_dir.is_dir():
            continue
        candidate = date_dir / f"{safe}.md"
        if candidate.is_file():
            return _load_from_path(candidate)
    return None


def list_trajectories(
    vault: Any,
    *,
    date_from: str | None = None,
    date_to: str | None = None,
) -> list[Trajectory]:
    """List trajectories under the optional inclusive date window."""
    if not vault.trajectories_dir.is_dir():
        return []
    out: list[Trajectory] = []
    for date_dir in sorted(vault.trajectories_dir.iterdir()):
        if not date_dir.is_dir():
            continue
        d_name = date_dir.name
        if date_from and d_name < date_from:
            continue
        if date_to and d_name > date_to:
            continue
        for md in sorted(date_dir.glob("*.md")):
            try:
                traj = _load_from_path(md)
            except Exception:  # noqa: BLE001 - one bad file must not abort listing
                continue
            if traj is not None:
                out.append(traj)
    return out
