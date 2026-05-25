"""Staging capture layer for the PostToolUse hook.

Captures tool events on the critical path with no LLM call. Applies
``<private>...</private>`` redaction (constraint C4), writes a privacy
audit entry per redaction, enforces a configurable rolling size cap
(default 100 MB) by archiving the oldest files, and writes the event
to a per-minute JSONL file.

Designed to never block the user: ``capture_event`` swallows all
exceptions and returns ``False`` on failure. The hook script that
wraps this module also exits with status zero unconditionally.

Public API:

- ``StagingConfig``: paths, host identifier, size cap, and the set of
  tool names that should be captured.
- ``capture_event(event, config)``: filter, redact, hash, write. Returns
  True if the event was written, False otherwise.
- ``redact_private(event, ...)``, ``enforce_size_cap(...)``,
  ``compute_content_hash(...)``: lower-level helpers exposed for tests
  and downstream consumers.
"""

from __future__ import annotations

import hashlib
import json
import socket
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ..privacy import redact as _privacy_redact

DEFAULT_CAPTURE_TOOLS: frozenset[str] = frozenset(
    {"Edit", "Write", "Bash", "Task", "MultiEdit"}
)
DEFAULT_SIZE_CAP_BYTES: int = 100 * 1024 * 1024  # 100 MB


def _short_hostname() -> str:
    return socket.gethostname().split(".")[0]


def _now() -> datetime:
    return datetime.now(UTC)


def _now_iso() -> str:
    return _now().isoformat()


def _sha256_first16(s: str) -> str:
    """First 16 hex chars of SHA256. Used for dedup hashes and audit hashes."""
    return hashlib.sha256(s.encode("utf-8")).hexdigest()[:16]


@dataclass
class StagingConfig:
    """Configuration for the staging capture layer.

    Attributes:
        staging_dir: root directory where captured JSONL files are
            written. Typically ``vault/.mneme/staging`` from
            ``VaultConfig.staging_dir``.
        audit_dir: root directory for privacy audit entries. Each
            redaction writes one record. Typically
            ``vault/.mneme/audit``.
        host: short hostname used as the second path component under
            ``staging_dir``. Defaults to the local short hostname.
        capture_tools: tool names whose events should be captured.
            Events for any other tool are silently ignored.
        size_cap_bytes: rolling size cap for the staging directory.
            When exceeded, the oldest files are moved into an
            ``archive/`` subdirectory until usage falls below 90 percent
            of the cap.
    """

    staging_dir: Path
    audit_dir: Path
    host: str = field(default_factory=_short_hostname)
    capture_tools: frozenset[str] = DEFAULT_CAPTURE_TOOLS
    size_cap_bytes: int = DEFAULT_SIZE_CAP_BYTES


def _redact_value(
    value: Any,
    field_path: str,
    audit: list[dict[str, Any]],
    config: StagingConfig,
) -> Any:
    """Recursively redact ``<private>...</private>`` from any nested value.

    Phase J post-Codex-review fix: the prior implementation only scanned
    top-level string fields, so nested ``tool_input``, ``tool_response``,
    ``args``, and array payloads bypassed redaction. This walker traverses
    dicts and lists in addition to strings, records the full dotted field
    path for each redaction, and leaves non-text scalars untouched.

    Redaction is delegated to ``privacy.redact`` which provides
    case-insensitive, attribute-tolerant, fail-closed semantics. The audit
    record preserves the original length and a SHA256-first-16 hash of the
    original matched span; we detect whether a redaction occurred by
    comparing before/after string identity.
    """
    if isinstance(value, str):
        redacted = _privacy_redact(value)
        if redacted != value:
            # Count how many [REDACTED] tokens were inserted by measuring the
            # difference between the two strings at the token level. For the
            # audit we need at least one record per call-site; emit one record
            # that covers the full field with the hash of the original text.
            audit.append(
                {
                    "ts": _now_iso(),
                    "host": config.host,
                    "field": field_path,
                    "original_length": len(value),
                    "audit_hash": _sha256_first16(value),
                }
            )
        return redacted
    if isinstance(value, dict):
        return {
            k: _redact_value(v, f"{field_path}.{k}" if field_path else str(k), audit, config)
            for k, v in value.items()
        }
    if isinstance(value, list):
        return [
            _redact_value(item, f"{field_path}[{i}]", audit, config)
            for i, item in enumerate(value)
        ]
    return value


def redact_private(event: dict[str, Any], config: StagingConfig) -> dict[str, Any]:
    """Replace ``<private>...</private>`` sections recursively.

    Walks the event dict, including nested dicts and lists, and replaces
    every match in any string scalar. Each redaction emits one audit
    record (timestamp, host, dotted field path, original length,
    SHA256-first-16 hash). The audit log never contains the original
    text, only the hash and length.
    """
    audit: list[dict[str, Any]] = []
    redacted = _redact_value(event, "", audit, config)
    if audit:
        _write_audit(audit, config)
    # _redact_value returns a new dict, but the public contract has always
    # mutated the input. Copy keys back to preserve identity for callers
    # that hold the original reference.
    if isinstance(redacted, dict):
        event.clear()
        event.update(redacted)
    return event


def _write_audit(entries: Iterable[dict[str, Any]], config: StagingConfig) -> None:
    config.audit_dir.mkdir(parents=True, exist_ok=True)
    today = _now().strftime("%Y-%m-%d")
    out = config.audit_dir / f"{today}.jsonl"
    with out.open("a", encoding="utf-8") as fp:
        for e in entries:
            fp.write(json.dumps(e, ensure_ascii=False) + "\n")


def enforce_size_cap(config: StagingConfig) -> int:
    """Archive oldest staging files until usage falls below 90 percent of cap.

    Returns the number of files archived. Files under any ``archive``
    subdirectory are never re-archived.
    """
    if not config.staging_dir.exists():
        return 0
    all_files = [
        f
        for f in config.staging_dir.rglob("*.jsonl")
        if "archive" not in f.parts
    ]
    total = sum(f.stat().st_size for f in all_files)
    if total < config.size_cap_bytes:
        return 0
    archive_root = config.staging_dir / "archive"
    archive_root.mkdir(exist_ok=True)
    sorted_files = sorted(all_files, key=lambda f: f.stat().st_mtime)
    target = int(config.size_cap_bytes * 0.9)
    archived = 0
    while total > target and sorted_files:
        f = sorted_files.pop(0)
        try:
            size = f.stat().st_size
            relative = f.relative_to(config.staging_dir)
            # Path.replace overwrites destination atomically on POSIX
            # and Windows, unlike Path.rename which raises on Windows
            # when the destination already exists.
            archive_path = archive_root / relative
            archive_path.parent.mkdir(parents=True, exist_ok=True)
            f.replace(archive_path)
            total -= size
            archived += 1
        except (FileNotFoundError, OSError):
            continue
    return archived


def compute_content_hash(event: dict[str, Any]) -> str:
    """Stable hash for downstream deduplication windows."""
    parts = json.dumps(event, sort_keys=True, default=str, ensure_ascii=False)
    return _sha256_first16(parts)


def _resolve_tool_name(event: dict[str, Any]) -> str:
    name = event.get("tool_name") or event.get("tool") or ""
    return str(name)


def capture_event(event: dict[str, Any], config: StagingConfig) -> bool:
    """Filter, redact, hash, and write a tool event.

    Returns True on successful write. Returns False if the tool is not
    in the capture set, the input is empty, or any unexpected exception
    occurred. The function never raises; failures are silent because
    this runs on the critical path.
    """
    try:
        if not isinstance(event, dict) or not event:
            return False
        if _resolve_tool_name(event) not in config.capture_tools:
            return False
        enforce_size_cap(config)
        event = redact_private(event, config)
        _write_event(event, config)
        return True
    except Exception:
        return False


def _write_event(event: dict[str, Any], config: StagingConfig) -> None:
    now = _now()
    out_dir = config.staging_dir / config.host / now.strftime("%Y-%m-%d")
    out_dir.mkdir(parents=True, exist_ok=True)
    out_file = out_dir / f"{now.strftime('%H-%M')}-events.jsonl"

    event = dict(event)
    event["captured_at"] = now.isoformat()
    event["host"] = config.host
    event["content_hash"] = compute_content_hash(event)

    with out_file.open("a", encoding="utf-8") as fp:
        fp.write(json.dumps(event, ensure_ascii=False) + "\n")
