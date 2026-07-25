"""Per-backend retrieval telemetry emitter.

Emits RetrievalEvent records as JSONL lines to vault.telemetry_dir.
The query is stored as a per-vault HMAC-SHA256, never raw text.
Uses the same append pattern as the existing telemetry writer but
writes to a dedicated retrieval-YYYY-MM-DD.jsonl file.

Design decisions:
- Never raises: all errors are wrapped in try/except.
- query_hash = HMAC-SHA256(per-vault key, normalized_query).
- Output file: vault.telemetry_dir / retrieval-YYYY-MM-DD.jsonl
- Append semantics: each call adds one JSON line.
- atomic_write is NOT used here because atomic_write replaces the whole
  file; for an append-only JSONL telemetry log, direct open("a") is
  correct (same pattern as existing telemetry.writer). Partial writes on
  a single JSON line are tolerably rare for a best-effort telemetry log.
"""

from __future__ import annotations

import errno
import hmac
import json
import os
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, TypedDict

from ..privacy import redact
from ..vault.config import VaultConfig

_HMAC_KEY_FILE = "telemetry-hmac.key"
_HMAC_KEY_BYTES = 32


class BackendTelemetryStatus(TypedDict):
    """Canonical outcome flags for one attempted retrieval backend."""

    attempted: bool
    succeeded: bool
    failed: bool
    contributed: bool


@dataclass
class RetrievalEvent:
    """Telemetry record for one retrieve() call.

    Attributes:
        query_hash: normalized query text or a caller-provided identifier.
            The sink replaces it with a per-vault HMAC-SHA256 digest.
            Never the raw query text.
        backends: ordered list of backends that were activated.
        per_backend_hits: number of hits returned by each backend.
        per_backend_latency_ms: wall-clock latency for each backend in ms.
        fused_count: number of hits after RRF fusion.
        top1_source: source path of the top-ranked hit, or None if no hits.
        timestamp_iso: ISO 8601 timestamp of the event (UTC).
    """

    query_hash: str
    backends: list[str]
    per_backend_hits: dict[str, int]
    per_backend_latency_ms: dict[str, float]
    per_backend_status: dict[str, BackendTelemetryStatus]
    fused_count: int
    top1_source: str | None
    timestamp_iso: str


def _today_iso() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%d")


def _read_hmac_key(key_path: Path) -> bytes:
    data = key_path.read_bytes()
    if len(data) != _HMAC_KEY_BYTES:
        raise ValueError(
            f"{_HMAC_KEY_FILE} is {len(data)} bytes; expected {_HMAC_KEY_BYTES}"
        )
    return data


def _load_or_create_hmac_key(vault: VaultConfig) -> bytes:
    """Load the per-vault telemetry key or create it exclusively."""
    state_dir = vault.state_dir
    key_path = state_dir / _HMAC_KEY_FILE
    if key_path.exists():
        if key_path.is_symlink():
            raise ValueError(f"refusing telemetry key symlink: {key_path}")
        return _read_hmac_key(key_path)

    state_dir.mkdir(parents=True, exist_ok=True)
    key = secrets.token_bytes(_HMAC_KEY_BYTES)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0)
    try:
        fd = os.open(key_path, flags, 0o600)
    except OSError as exc:
        if exc.errno not in {errno.EEXIST, errno.EACCES, errno.EPERM}:
            raise
        if key_path.is_symlink():
            raise ValueError(f"refusing telemetry key symlink: {key_path}") from exc
        return _read_hmac_key(key_path)

    with os.fdopen(fd, "wb") as fp:
        fp.write(key)
        fp.flush()
        os.fsync(fp.fileno())
    return key


def _safe_query_hash(vault: VaultConfig, value: str) -> str:
    """Return a vault-specific HMAC and never an unkeyed digest."""
    key = _load_or_create_hmac_key(vault)
    return hmac.new(key, value.encode("utf-8"), "sha256").hexdigest()


def emit_retrieval_event(vault: VaultConfig, event: RetrievalEvent) -> None:
    """Append one RetrievalEvent as a JSON line to the daily telemetry file.

    Target path: vault.telemetry_dir / retrieval-YYYY-MM-DD.jsonl

    Never raises. All I/O errors are swallowed so callers on the
    retrieval hot path are not interrupted by telemetry failures.
    """
    try:
        telemetry_dir = vault.telemetry_dir
        telemetry_dir.mkdir(parents=True, exist_ok=True)
        date_str = _today_iso()
        jsonl_path = telemetry_dir / f"retrieval-{date_str}.jsonl"
        record: dict[str, Any] = {
            "query_hash": _safe_query_hash(vault, event.query_hash),
            "backends": [redact(backend) for backend in event.backends],
            "per_backend_hits": {
                redact(backend): hits
                for backend, hits in event.per_backend_hits.items()
            },
            "per_backend_latency_ms": {
                redact(backend): latency
                for backend, latency in event.per_backend_latency_ms.items()
            },
            "per_backend_status": {
                redact(backend): {
                    "attempted": status["attempted"],
                    "succeeded": status["succeeded"],
                    "failed": status["failed"],
                    "contributed": status["contributed"],
                }
                for backend, status in event.per_backend_status.items()
            },
            "fused_count": event.fused_count,
            "top1_source": (
                redact(event.top1_source)
                if event.top1_source is not None
                else None
            ),
            "timestamp_iso": redact(event.timestamp_iso),
        }
        line = json.dumps(record, ensure_ascii=False)
        with jsonl_path.open("a", encoding="utf-8") as fp:
            fp.write(line + "\n")
    except Exception:  # noqa: BLE001
        pass
