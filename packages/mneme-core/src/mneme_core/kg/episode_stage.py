"""Non-blocking PostToolUse stager for the KG worker.

When the ``kg_active_flag`` is present, ``stage_event`` writes the
captured PostToolUse payload to a per-host JSONL queue under the
vault's state directory. A separate out-of-band worker (``worker.py``)
drains the queue and calls ``Graphiti.add_episode`` with bi-temporal
metadata.

Sacred constraints honored here:

* **Non-blocking**: every public function swallows exceptions and
  returns. The PostToolUse hook is on the critical path; nothing the
  KG layer does is allowed to make Claude Code wait.
* **Privacy**: ``<private>...</private>`` content is replaced with a
  ``<REDACTED:HASH8>`` token before the record is written. The hash
  is short on purpose: it lets the operator correlate audit log
  entries without ever persisting the original text.
* **Idempotent**: the worker dedups by ``content_hash`` (full SHA256
  over the redacted payload). Re-staging the same event is safe.
"""

from __future__ import annotations

import hashlib
import json
import os
import socket
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ..privacy import redact_value as _privacy_redact_value

DEFAULT_CAPTURE_TOOLS: frozenset[str] = frozenset(
    {"Edit", "Write", "Bash", "Task", "MultiEdit"}
)


def _short_hostname() -> str:
    return socket.gethostname().split(".")[0]


def _now() -> datetime:
    return datetime.now(UTC)


@dataclass
class KgConfig:
    """Vault-derived configuration for the KG layer.

    Attributes:
        queue_dir: per-host JSONL queue root. Typically
            ``vault.kg_queue_dir``.
        processed_dir: archive root for drained queue files. Typically
            ``vault.kg_processed_dir``.
        active_flag: presence-gate. Every entrypoint no-ops when this
            file does not exist.
        credentials_path: Neo4j credentials JSON consumed by the live
            worker only.
        community_refresh_flag: flag the Stop hook writes to request a
            ``Graphiti.build_communities`` pass on the next drain.
        worker_log: append-only JSONL telemetry for worker runs.
        cost_ledger: opt-in cost-cap ledger shared with the compression
            pipeline (Phase F). Stored as a path so the worker can
            stamp episode-add costs into it when an LLM provider key
            is present.
        cost_cap_usd_monthly: configurable spend ceiling. ``None`` means
            no enforced cap; the worker still records spend.
        host: short hostname used as the second path component.
        capture_tools: whitelist of tool names that get staged.
    """

    queue_dir: Path
    processed_dir: Path
    active_flag: Path
    credentials_path: Path
    community_refresh_flag: Path
    worker_log: Path
    cost_ledger: Path
    cost_cap_usd_monthly: float | None = None
    host: str = field(default_factory=_short_hostname)
    capture_tools: frozenset[str] = DEFAULT_CAPTURE_TOOLS



def _resolve_tool_name(event: dict[str, Any]) -> str:
    name = event.get("tool_name") or event.get("tool") or ""
    return str(name)


def is_active(config: KgConfig) -> bool:
    """True when the active-flag is present. Used by hooks to gate the call."""
    try:
        return config.active_flag.is_file()
    except OSError:
        return False


def kg_config_from_vault(
    vault: Any,
    *,
    cost_cap_usd_monthly: float | None = None,
    capture_tools: frozenset[str] | None = None,
) -> KgConfig:
    """Build a ``KgConfig`` from a resolved ``VaultConfig``.

    Lives here (not in ``vault.config``) so that lite/standard
    profiles do not have a hard import path between ``vault.config``
    and the KG module. The argument is typed as ``Any`` to avoid a
    circular import; in practice it is always a ``VaultConfig``.
    """
    return KgConfig(
        queue_dir=vault.kg_queue_dir,
        processed_dir=vault.kg_processed_dir,
        active_flag=vault.kg_active_flag,
        credentials_path=vault.kg_credentials_path,
        community_refresh_flag=vault.kg_community_refresh_flag,
        worker_log=vault.kg_worker_log,
        cost_ledger=vault.kg_cost_ledger,
        cost_cap_usd_monthly=cost_cap_usd_monthly,
        capture_tools=capture_tools if capture_tools is not None else DEFAULT_CAPTURE_TOOLS,
    )


def stage_event(event: dict[str, Any], config: KgConfig) -> bool:
    """Write one redacted JSONL record. Returns True on write, False otherwise.

    Never raises. The PostToolUse hook calls this on the critical path
    and is required to exit zero even if the staging directory is
    unwritable or the payload is malformed.
    """
    try:
        if not isinstance(event, dict) or not event:
            return False
        if not is_active(config):
            return False
        if _resolve_tool_name(event) not in config.capture_tools:
            return False

        now = _now()
        out_dir = config.queue_dir / config.host / now.strftime("%Y-%m-%d")
        out_dir.mkdir(parents=True, exist_ok=True)
        out_file = out_dir / f"{now.strftime('%H')}-events.jsonl"

        # Redact field-by-field before serialisation so that fail-closed
        # redaction on an unbalanced <private> tag never corrupts the JSON
        # structure (redacting the serialised blob could eat closing quotes).
        redacted_event: dict[str, Any] = _privacy_redact_value(event)
        # Replace canonical [REDACTED] tokens with the hash-tagged form the
        # KG worker expects, so operators can correlate audit entries.
        original_blob = json.dumps(event, ensure_ascii=False, default=str)
        h = hashlib.sha256(original_blob.encode("utf-8")).hexdigest()[:8]
        safe_blob = json.dumps(redacted_event, ensure_ascii=False, default=str).replace(
            "[REDACTED]", f"<REDACTED:{h}>"
        )
        content_hash = hashlib.sha256(safe_blob.encode("utf-8")).hexdigest()

        record = {
            "event_id": f"{now.strftime('%H%M%S')}-{content_hash[:12]}",
            "ts": now.isoformat(),
            "host": config.host,
            "pid": os.getpid(),
            "content_hash": content_hash,
            "payload": json.loads(safe_blob),
            "staged_by": "mneme.kg.episode_stage",
            "schema_version": "1.0",
        }
        with out_file.open("a", encoding="utf-8") as fp:
            fp.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
        return True
    except Exception:
        return False
