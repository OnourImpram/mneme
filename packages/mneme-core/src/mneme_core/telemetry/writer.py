"""Telemetry record writer with bring-your-own-patterns PII redaction.

Writes JSONL telemetry records grouped by event class and date. Applies
regex-based PII redaction before any write, so the on-disk record
never contains matched content. The audit log contains only the names
of patterns that fired, never the original text.

Design decisions:

- **Opt-in redaction**: ``TelemetryConfig.pii_patterns`` defaults to
  an empty tuple. False positives have a real productivity cost, so
  the library does not enable patterns silently.
- **Composable defaults**: the module exposes
  ``COMMON_GENERIC_CREDENTIAL_PATTERNS`` and
  ``COMMON_PII_TURKISH_NATIONAL_ID`` as constants users can combine
  into their own pattern set.
- **No identifier lookups for sensitive values**: the writer never
  reads from the local Anthropic API key, env credentials, or any
  filesystem path outside the configured telemetry and audit dirs.
- **Graceful failure**: file writes that fail (full disk, permissions)
  do not raise. ``write_event`` returns ``False``.

The writer is path-agnostic. Pass ``vault.telemetry_dir`` from
``VaultConfig`` to keep records inside the vault's derived state
directory.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import socket

VALID_EVENT_CLASSES: frozenset[str] = frozenset(
    {
        "tool_use",
        "mcp_call",
        "hook",
        "llm_call",
        "error",
        "plugin_event",
    }
)

PiiPattern = tuple[re.Pattern[str], str]

# Generic credential assignments. Matches `password: foo`,
# `api_key = bar`, `secret=baz`, `token: qux`. Case-insensitive.
# Does NOT match any specific credential signature, only the shape.
COMMON_GENERIC_CREDENTIAL_PATTERNS: tuple[PiiPattern, ...] = (
    (
        re.compile(
            r"\b(?:password|api[_-]?key|secret|token|auth[_-]?token)\s*[:=]\s*\S+",
            re.IGNORECASE,
        ),
        "[CREDENTIAL-REDACTED]",
    ),
)

# Turkish national ID format: 11 digits, first digit non-zero.
# Locale-specific; users include this only when relevant.
COMMON_PII_TURKISH_NATIONAL_ID: tuple[PiiPattern, ...] = (
    (re.compile(r"\b[1-9]\d{10}\b"), "[TR-ID-REDACTED]"),
)


def _short_hostname() -> str:
    return socket.gethostname().split(".")[0]


def _now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class TelemetryConfig:
    """Configuration for the telemetry writer.

    Attributes:
        telemetry_dir: root directory for telemetry JSONL files.
            Records are placed under ``{telemetry_dir}/{host}/{date}/``.
        audit_dir: root directory for privacy audit entries. Each
            redaction emits a record naming the patterns that fired.
        host: short hostname used as a path component. Defaults to the
            local short hostname.
        pii_patterns: redaction pattern set. Defaults to empty (no
            redaction). Compose using the ``COMMON_*`` module-level
            constants plus project-specific patterns.
    """

    telemetry_dir: Path
    audit_dir: Path
    host: str = field(default_factory=_short_hostname)
    pii_patterns: tuple[PiiPattern, ...] = ()


def redact_pii(
    text: str,
    patterns: tuple[PiiPattern, ...],
    audit_callback: Callable[[list[str]], None] | None = None,
) -> str:
    """Apply each pattern in order. Returns the redacted string.

    Non-string inputs are returned unchanged via the wrapper in
    ``write_event``; this function expects a string.

    If any pattern matches, the names (regex source) of all matching
    patterns are passed to ``audit_callback`` as a list. The callback
    is responsible for persisting the audit entry; this keeps
    ``redact_pii`` pure and easy to test.
    """
    matched: list[str] = []
    for pattern, replacement in patterns:
        if pattern.search(text):
            matched.append(pattern.pattern)
            text = pattern.sub(replacement, text)
    if matched and audit_callback is not None:
        audit_callback(matched)
    return text


def _log_audit(matched_patterns: list[str], config: TelemetryConfig) -> None:
    try:
        config.audit_dir.mkdir(parents=True, exist_ok=True)
        out = config.audit_dir / f"{_now().strftime('%Y-%m-%d')}.jsonl"
        with out.open("a", encoding="utf-8") as fp:
            fp.write(
                json.dumps(
                    {
                        "ts": _now().isoformat(),
                        "host": config.host,
                        "patterns_matched": matched_patterns,
                    },
                    ensure_ascii=False,
                )
                + "\n"
            )
    except OSError:
        pass


def _file_for_event_class(
    event_class: str,
    session_id: str,
) -> str:
    if event_class == "tool_use":
        return "tool-usage.jsonl"
    if event_class == "mcp_call":
        return "mcp-calls.jsonl"
    if event_class == "hook":
        return f"session-{session_id}.jsonl"
    if event_class == "llm_call":
        return "token-ledger.jsonl"
    if event_class == "error":
        return "errors.jsonl"
    if event_class == "plugin_event":
        return "plugin-events.jsonl"
    return "events.jsonl"


def write_event(
    event_class: str,
    name: str,
    config: TelemetryConfig,
    session_id: str = "unknown",
    status: str = "success",
    **kwargs: Any,
) -> bool:
    """Write one telemetry record to the appropriate JSONL file.

    Args:
        event_class: must be in ``VALID_EVENT_CLASSES``. Other values
            cause the call to return False without writing.
        name: human-readable event name. Passes through redaction.
        config: paths, host, and PII patterns.
        session_id: routed into the hook file name when class is
            ``hook``. Defaults to ``"unknown"``.
        status: defaults to ``"success"``. Records with status
            ``"error"`` and any class other than ``"error"`` are also
            duplicated into ``errors.jsonl`` for easy triage.
        **kwargs: extra fields merged into the record. String values
            pass through redaction.

    Returns:
        True on successful write, False on invalid class or any OSError
        during file write.
    """
    if event_class not in VALID_EVENT_CLASSES:
        return False

    now = _now()

    def audit_cb(matched: list[str]) -> None:
        _log_audit(matched, config)

    redacted_name = redact_pii(name, config.pii_patterns, audit_cb)
    record: dict[str, Any] = {
        "ts": now.isoformat(),
        "session_id": session_id,
        "host": config.host,
        "event_class": event_class,
        "name": redacted_name,
        "status": status,
    }
    for k, v in kwargs.items():
        if isinstance(v, str):
            record[k] = redact_pii(v, config.pii_patterns, audit_cb)
        else:
            record[k] = v

    target_file = _file_for_event_class(event_class, session_id)
    out_dir = config.telemetry_dir / config.host / now.strftime("%Y-%m-%d")
    try:
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / target_file
        with out_path.open("a", encoding="utf-8") as fp:
            fp.write(json.dumps(record, ensure_ascii=False) + "\n")
        if status == "error" and event_class != "error":
            err_path = out_dir / "errors.jsonl"
            with err_path.open("a", encoding="utf-8") as fp:
                fp.write(json.dumps(record, ensure_ascii=False) + "\n")
    except OSError:
        return False
    return True
