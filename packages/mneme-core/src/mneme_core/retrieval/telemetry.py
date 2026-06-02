"""Per-backend retrieval telemetry emitter.

Emits RetrievalEvent records as JSONL lines to vault.telemetry_dir.
The query is stored as sha256(normalized_query), never raw text.
Uses the same append pattern as the existing telemetry writer but
writes to a dedicated retrieval-YYYY-MM-DD.jsonl file.

Design decisions:
- Never raises: all errors are wrapped in try/except.
- query_hash = sha256(normalized_query), not raw query text.
- Output file: vault.telemetry_dir / retrieval-YYYY-MM-DD.jsonl
- Append semantics: each call adds one JSON line.
- atomic_write is NOT used here because atomic_write replaces the whole
  file; for an append-only JSONL telemetry log, direct open("a") is
  correct (same pattern as existing telemetry.writer). Partial writes on
  a single JSON line are tolerably rare for a best-effort telemetry log.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from ..vault.config import VaultConfig


@dataclass
class RetrievalEvent:
    """Telemetry record for one retrieve() call.

    Attributes:
        query_hash: sha256 hex digest of the normalized query string.
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
    fused_count: int
    top1_source: str | None
    timestamp_iso: str


def _today_iso() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%d")


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
            "query_hash": event.query_hash,
            "backends": event.backends,
            "per_backend_hits": event.per_backend_hits,
            "per_backend_latency_ms": event.per_backend_latency_ms,
            "fused_count": event.fused_count,
            "top1_source": event.top1_source,
            "timestamp_iso": event.timestamp_iso,
        }
        line = json.dumps(record, ensure_ascii=False)
        with jsonl_path.open("a", encoding="utf-8") as fp:
            fp.write(line + "\n")
    except Exception:  # noqa: BLE001
        pass
