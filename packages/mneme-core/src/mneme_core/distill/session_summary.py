"""Deterministic extractive session summary (zero-LLM, default ON).

This is the Stop-path distillation layer: when a session ends, the Stop
hook asks this module for a structured summary of what happened. The
summary is **extractive and deterministic** — it is computed from the
already-redacted staging JSONL records written by PostToolUse, plus a
bounded read of the Claude Code transcript head for the session intent.
No LLM, no network, no clock dependency beyond the staging timestamps,
so it honours the Zero-LLM-Stop sacred constraint while giving every
session a useful summary out of the box.

The opt-in LLM compression pipeline (``compression/``) remains the
richer alternative; this module is the floor, not the ceiling.

Privacy: staging records are redacted at capture time (constraint C4),
and the rendered summary is passed through :func:`privacy.redact` once
more before it is returned, so a ``<private>`` span can never survive
into the session log even if it arrived via the transcript head.

Config lives at ``<state_dir>/summary.json``::

    {"deterministic": true, "language": "en"}

An absent or malformed file means **enabled, English** — default ON is
the contract. Localized templates live in ``distill/templates/`` as
``summary-<lang>.md`` and are plain ``str.format`` documents so adding
a language is a data change, not a code change.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from ..privacy import redact as _redact
from ..vault.config import VaultConfig

SUMMARY_CONFIG_FILENAME = "summary.json"
_TEMPLATE_DIR = Path(__file__).resolve().parent / "templates"
_FALLBACK_LANGUAGE = "en"

# Tool-input keys that can carry a file path, in priority order.
_FILE_PATH_KEYS = ("file_path", "path", "notebook_path")

# Hard bound on how much of the transcript head is read for the intent
# line. Keeps the Stop path O(1) on multi-hundred-MB transcripts.
_MAX_TRANSCRIPT_BYTES = 2 * 1024 * 1024

# Hard bound on staging bytes parsed per file, against pathological
# staging files. Records beyond the cap are ignored, never an error.
_MAX_STAGING_FILE_BYTES = 8 * 1024 * 1024

_MAX_INTENT_CHARS = 240


@dataclass(frozen=True)
class SummaryConfig:
    """Resolved deterministic-summary settings."""

    deterministic: bool = True
    language: str = _FALLBACK_LANGUAGE
    max_files: int = 12


@dataclass(frozen=True)
class SessionStats:
    """Aggregated, already-redacted facts about one session."""

    tool_counts: dict[str, int]
    files_touched: list[str]
    first_event_at: str
    last_event_at: str

    @property
    def total_events(self) -> int:
        return sum(self.tool_counts.values())


def load_summary_config(vault: VaultConfig) -> SummaryConfig:
    """Read ``summary.json`` from the vault state dir.

    Absent or malformed config returns the defaults (deterministic ON,
    English). This function never raises.
    """
    path = vault.state_dir / SUMMARY_CONFIG_FILENAME
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return SummaryConfig()
    if not isinstance(raw, dict):
        return SummaryConfig()
    deterministic = bool(raw.get("deterministic", True))
    language = str(raw.get("language") or _FALLBACK_LANGUAGE)
    try:
        max_files = int(raw.get("max_files", SummaryConfig().max_files))
    except (TypeError, ValueError):
        max_files = SummaryConfig().max_files
    if max_files < 1:
        max_files = SummaryConfig().max_files
    return SummaryConfig(
        deterministic=deterministic, language=language, max_files=max_files
    )


def _staging_day_dirs(vault: VaultConfig) -> list[Path]:
    """Today's and yesterday's staging day-directories across all hosts.

    Yesterday is included so a session spanning midnight still finds its
    early records. Missing directories are silently skipped.
    """
    staging = vault.staging_dir
    if not staging.is_dir():
        return []
    now = datetime.now(UTC)
    wanted = {now.strftime("%Y-%m-%d"), (now - timedelta(days=1)).strftime("%Y-%m-%d")}
    out: list[Path] = []
    try:
        for host_dir in staging.iterdir():
            if not host_dir.is_dir():
                continue
            for day in wanted:
                candidate = host_dir / day
                if candidate.is_dir():
                    out.append(candidate)
    except OSError:
        return out
    return out


def _iter_session_records(vault: VaultConfig, session_id: str) -> list[dict[str, Any]]:
    """Parse staging JSONL records belonging to *session_id*.

    Tolerates broken lines, undecodable bytes, and oversized files.
    Never raises.
    """
    records: list[dict[str, Any]] = []
    for day_dir in _staging_day_dirs(vault):
        try:
            files = sorted(day_dir.glob("*-events.jsonl"))
        except OSError:
            continue
        for f in files:
            try:
                blob = f.read_bytes()[:_MAX_STAGING_FILE_BYTES]
            except OSError:
                continue
            for line in blob.decode("utf-8", errors="replace").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(rec, dict) and str(rec.get("session_id") or "") == session_id:
                    records.append(rec)
    return records


def _collect_stats(records: list[dict[str, Any]]) -> SessionStats:
    tool_counts: dict[str, int] = {}
    files: list[str] = []
    seen_files: set[str] = set()
    timestamps: list[str] = []
    for rec in records:
        tool = str(rec.get("tool_name") or "unknown")
        tool_counts[tool] = tool_counts.get(tool, 0) + 1
        captured = rec.get("captured_at")
        if isinstance(captured, str) and captured:
            timestamps.append(captured)
        tool_input = rec.get("tool_input")
        if isinstance(tool_input, dict):
            for key in _FILE_PATH_KEYS:
                value = tool_input.get(key)
                if isinstance(value, str) and value and value not in seen_files:
                    seen_files.add(value)
                    files.append(value)
                    break
    timestamps.sort()
    return SessionStats(
        tool_counts=tool_counts,
        files_touched=files,
        first_event_at=timestamps[0] if timestamps else "",
        last_event_at=timestamps[-1] if timestamps else "",
    )


def _extract_text(content: object) -> str:
    """Pull plain text out of a Claude Code message ``content`` field.

    The field is either a plain string or a list of typed blocks; both
    shapes appear across Claude Code versions.
    """
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                text = block.get("text")
                if isinstance(text, str):
                    parts.append(text)
        return " ".join(parts)
    return ""


def _transcript_intent(transcript_path: str) -> str:
    """First user message from the transcript head, bounded and redacted."""
    if not transcript_path:
        return ""
    path = Path(transcript_path)
    try:
        if not path.is_file():
            return ""
        blob = path.read_bytes()[:_MAX_TRANSCRIPT_BYTES]
    except OSError:
        return ""
    for line in blob.decode("utf-8", errors="replace").splitlines():
        line = line.strip()
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
        if text:
            text = _redact(text)
            if len(text) > _MAX_INTENT_CHARS:
                text = text[: _MAX_INTENT_CHARS - 1] + "…"
            return text
    return ""


def _load_template(language: str) -> str:
    """Load ``summary-<lang>.md``, falling back to English.

    The English template ships with the package; if even that read
    fails the built-in minimal template keeps the Stop path alive.
    """
    for lang in (language, _FALLBACK_LANGUAGE):
        candidate = _TEMPLATE_DIR / f"summary-{lang}.md"
        try:
            return candidate.read_text(encoding="utf-8")
        except OSError:
            continue
    return "{intent_block}{files_block}{tools_block}"


def _render(
    stats: SessionStats,
    intent: str,
    config: SummaryConfig,
) -> str:
    template = _load_template(config.language)

    labels_tr = config.language == "tr"

    if intent:
        intent_label = "Oturum amaci" if labels_tr else "Session intent"
        intent_block = f"**{intent_label}**: {intent}\n\n"
    else:
        intent_block = ""

    shown = stats.files_touched[: config.max_files]
    overflow = len(stats.files_touched) - len(shown)
    if shown:
        lines = "\n".join(f"- `{p}`" for p in shown)
        if overflow > 0:
            more = f"- … +{overflow}"
            lines = f"{lines}\n{more}"
        files_block = f"{lines}\n"
    else:
        files_block = "- —\n"

    tool_lines = "\n".join(
        f"- {name}: {count}"
        for name, count in sorted(stats.tool_counts.items(), key=lambda kv: (-kv[1], kv[0]))
    )
    tools_block = f"{tool_lines}\n" if tool_lines else "- —\n"

    rendered = template.format(
        intent_block=intent_block,
        files_block=files_block,
        tools_block=tools_block,
        total_events=stats.total_events,
        first_event_at=stats.first_event_at or "—",
        last_event_at=stats.last_event_at or "—",
    )
    # Belt and braces: the inputs are already redacted, but the rendered
    # document is what actually lands in the vault, so redact it whole.
    return _redact(rendered)


def build_session_summary(
    vault: VaultConfig,
    session_id: str,
    transcript_path: str,
) -> str | None:
    """Return the deterministic summary body for *session_id*, or ``None``.

    ``None`` means "nothing to say or feature disabled" and tells the
    Stop hook to fall back to its placeholder line. This function never
    raises: any unexpected failure degrades to ``None`` so the Stop
    ritual always completes.
    """
    try:
        config = load_summary_config(vault)
        if not config.deterministic:
            return None
        records = _iter_session_records(vault, session_id)
        intent = _transcript_intent(transcript_path)
        if not records and not intent:
            return None
        stats = _collect_stats(records)
        return _render(stats, intent, config)
    except Exception:
        return None
