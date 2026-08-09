"""Staging capture layer for the PostToolUse hook.

Captures tool events on the critical path with no LLM call. Elides the
whole-file ``tool_response.originalFile`` payload down to a digest,
applies ``<private>...</private>`` redaction (constraint C4), writes a
privacy audit entry per redaction, enforces a configurable rolling size cap
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
- ``redact_private(event, ...)``, ``summarize_bulky_response_fields(event)``,
  ``enforce_size_cap(...)``, ``compute_content_hash(...)``: lower-level
  helpers exposed for tests and downstream consumers.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import socket
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ..privacy import (
    redact as _privacy_redact,
)
from ..privacy import (
    redact_mapping_items as _privacy_redact_mapping_items,
)
from ..vault.atomic_write import atomic_write_text

DEFAULT_CAPTURE_TOOLS: frozenset[str] = frozenset(
    {"Edit", "Write", "Bash", "Task", "MultiEdit"}
)
DEFAULT_SIZE_CAP_BYTES: int = 100 * 1024 * 1024  # 100 MB
_SIZE_COUNTER_NAME: str = "size_counter.json"

# Edit/Write tool responses carry ``originalFile``: the *entire* pre-edit file
# content, duplicated into staging on every single edit. Nothing reads it —
# ``structuredPatch`` plus ``oldString``/``newString`` already describe the
# change — so it is pure ballast, and on a real backlog it measured as the
# largest single string field in ``tool_response``.
BULKY_RESPONSE_FIELD: str = "originalFile"
OMITTED_RESPONSE_FIELD: str = "originalFileOmitted"


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
        reconcile_every_n_events: how many captured events between forced
            full rglob reconciliations of the size counter. A full scan
            also triggers whenever the running byte total reaches or
            exceeds ``size_cap_bytes``. Lower values trade latency for
            tighter cap accuracy; the default of 100 bounds any counter
            drift to at most 100 events worth of data.
    """

    staging_dir: Path
    audit_dir: Path
    host: str = field(default_factory=_short_hostname)
    capture_tools: frozenset[str] = DEFAULT_CAPTURE_TOOLS
    size_cap_bytes: int = DEFAULT_SIZE_CAP_BYTES
    reconcile_every_n_events: int = 100


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
        redacted_dict: dict[Any, Any] = {}
        for original_key, safe_key, item in _privacy_redact_mapping_items(value):
            child_path = f"{field_path}.{safe_key}" if field_path else str(safe_key)
            if isinstance(original_key, str) and safe_key != original_key:
                audit.append(
                    {
                        "ts": _now_iso(),
                        "host": config.host,
                        "field": f"{child_path}.[key]",
                        "original_length": len(original_key),
                        "audit_hash": _sha256_first16(original_key),
                    }
                )
            redacted_dict[safe_key] = _redact_value(
                item,
                child_path,
                audit,
                config,
            )
        return redacted_dict
    if isinstance(value, list):
        return [
            _redact_value(item, f"{field_path}[{i}]", audit, config)
            for i, item in enumerate(value)
        ]
    return value


def summarize_bulky_response_fields(event: dict[str, Any]) -> bool:
    """Replace ``tool_response.originalFile`` with a digest and a byte count.

    Mutates ``event`` in place and returns True when a substitution happened.
    The content is dropped; what remains is::

        "originalFileOmitted": {"sha256_16": "<16 hex>", "bytes": <int>}

    which is enough to tell whether two events saw the same file version, and
    to size what was elided, without storing the file itself.

    Runs *before* redaction, deliberately. Redaction only neutralises
    ``<private>`` spans and would otherwise have to walk the whole file body
    on the critical path; dropping the field first is both cheaper and
    strictly more private. The consequence is that no privacy-audit record is
    emitted for this field — correct, because the field never reaches disk.

    A non-string value is left untouched: this collapses one known payload
    shape, it is not a general pruner.
    """
    resp = event.get("tool_response")
    if not isinstance(resp, dict):
        return False
    value = resp.get(BULKY_RESPONSE_FIELD)
    if not isinstance(value, str):
        return False
    del resp[BULKY_RESPONSE_FIELD]
    resp[OMITTED_RESPONSE_FIELD] = {
        "sha256_16": _sha256_first16(value),
        "bytes": len(value.encode("utf-8")),
    }
    return True


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


def _counter_path(config: StagingConfig) -> Path:
    """Return the path to the size counter sidecar (never matched by rglob('*.jsonl'))."""
    return config.staging_dir / _SIZE_COUNTER_NAME


def _read_counter(config: StagingConfig) -> tuple[int, int]:
    """Return (running_bytes, events_since_reconcile) from the sidecar.

    Returns (0, reconcile_every_n_events) on any read/parse failure so the
    caller immediately triggers a full reconciliation.
    """
    path = _counter_path(config)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return int(data["running_bytes"]), int(data["events_since_reconcile"])
    except Exception:
        return 0, config.reconcile_every_n_events


def _write_counter(
    config: StagingConfig, running_bytes: int, events_since_reconcile: int
) -> None:
    """Persist the size counter sidecar atomically."""
    payload = json.dumps(
        {
            "running_bytes": running_bytes,
            "events_since_reconcile": events_since_reconcile,
        },
        ensure_ascii=False,
    )
    with contextlib.suppress(Exception):
        atomic_write_text(_counter_path(config), payload)


def _measure_staging_bytes(config: StagingConfig) -> int:
    """Full rglob scan — same logic as enforce_size_cap uses internally."""
    if not config.staging_dir.exists():
        return 0
    return sum(
        f.stat().st_size
        for f in config.staging_dir.rglob("*.jsonl")
        if "archive" not in f.parts
    )


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
    """Stable hash for downstream deduplication windows.

    The hash is intentionally computed over the *content* of the event —
    the fields that identify what happened — and must exclude any fields
    that vary with capture time or capture host (``captured_at``,
    ``host``, ``content_hash``). This makes the hash stable across
    repeated captures of the same logical event, enabling cross-time
    deduplication. Callers must pass the event *before* those ephemeral
    fields are injected (see ``_write_event``).
    """
    _EPHEMERAL = frozenset({"captured_at", "host", "content_hash"})
    payload = {k: v for k, v in event.items() if k not in _EPHEMERAL}
    parts = json.dumps(payload, sort_keys=True, default=str, ensure_ascii=False)
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

    The size cap is enforced via a lightweight running byte counter
    (persisted in a JSON sidecar next to the staging directory) rather
    than an rglob scan on every event. A full reconciliation via
    ``enforce_size_cap`` is triggered only when the running byte total
    reaches the cap, or every ``config.reconcile_every_n_events`` events,
    whichever comes first. This keeps per-event latency O(1) in the common
    case while bounding any counter drift to at most N events.
    """
    try:
        if not isinstance(event, dict) or not event:
            return False
        if _resolve_tool_name(event) not in config.capture_tools:
            return False

        # Nested in its own guard: shrinking the payload is an optimisation,
        # never a reason to lose the event. If it fails the raw field is kept
        # and capture proceeds exactly as before.
        with contextlib.suppress(Exception):
            summarize_bulky_response_fields(event)

        event = redact_private(event, config)
        written_bytes = _write_event(event, config)

        # --- counter-gated size cap enforcement ---
        # Use the byte count returned by _write_event (which includes the
        # ephemeral fields injected after redaction plus the trailing newline)
        # so the counter reflects actual disk growth rather than the smaller
        # pre-injection payload size.
        record_bytes = written_bytes
        running_bytes, events_since_reconcile = _read_counter(config)
        running_bytes += record_bytes
        events_since_reconcile += 1

        needs_reconcile = (
            running_bytes >= config.size_cap_bytes
            or events_since_reconcile >= config.reconcile_every_n_events
        )
        if needs_reconcile:
            enforce_size_cap(config)
            running_bytes = _measure_staging_bytes(config)
            events_since_reconcile = 0

        _write_counter(config, running_bytes, events_since_reconcile)
        return True
    except Exception:
        return False


def _write_event(event: dict[str, Any], config: StagingConfig) -> int:
    """Write the event to the per-minute JSONL file.

    Injects ``content_hash``, ``captured_at``, and ``host`` before
    serialisation. Returns the number of bytes actually written so that
    ``capture_event`` can credit the size counter with the real disk
    growth rather than the smaller pre-injection payload size.
    """
    now = _now()
    out_dir = config.staging_dir / config.host / now.strftime("%Y-%m-%d")
    out_dir.mkdir(parents=True, exist_ok=True)
    out_file = out_dir / f"{now.strftime('%H-%M')}-events.jsonl"

    event = dict(event)
    # Hash is computed over content-only fields BEFORE ephemeral metadata
    # (captured_at, host) are injected, so identical events captured at
    # different times or on different hosts produce the same content_hash.
    event["content_hash"] = compute_content_hash(event)
    event["captured_at"] = now.isoformat()
    event["host"] = config.host

    line = json.dumps(event, ensure_ascii=False) + "\n"
    # newline="" disables platform newline translation so the bytes on disk
    # exactly equal ``len(line.encode("utf-8"))`` on every OS (on Windows the
    # default text mode would expand "\n" to "\r\n", making the returned count
    # undercount real disk growth by one byte per event).
    with out_file.open("a", encoding="utf-8", newline="") as fp:
        fp.write(line)
    return len(line.encode("utf-8"))
