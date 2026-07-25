"""Out-of-band Graphiti queue drain worker.

This is **not** a Claude Code hook. It is a CLI tool that drains the
JSONL queue produced by ``episode_stage.stage_event`` into a Graphiti
instance backed by Neo4j. Three subcommands:

* ``drain_dry_run(config)`` parses the queue and reports counts. No
  LLM calls. Safe to run on any machine.
* ``drain_live(config)`` calls ``Graphiti.add_episode`` for each
  staged record. Lazily imports ``graphiti_core`` so the lite and
  standard install profiles never load it.
* ``community_refresh(config)`` calls ``Graphiti.build_communities``
  once. Honors the flag written by ``flush.mark_community_refresh``.

Cost discipline: each ``add_episode`` invokes an LLM. The configurable
``KgConfig.cost_cap_usd_monthly`` lets the operator set a ceiling.
``drain_live`` reserves the per-episode estimate against the shared
cost ledger *before* each provider call, under the same file lock the
compression pipeline uses, then settles the reservation to the actual
cost on success or rolls it back on failure. Reserving before spending
closes the check-then-act race where two concurrent drains could both
pass the cap check and jointly overspend. The cap is scoped to the
``kg_add_episode`` kind so KG spend and compression spend each get
their own ceiling in the shared ``KgConfig.cost_ledger``. Episodes that
would breach the cap are skipped and counted in ``skipped_by_cost_cap``.
"""

from __future__ import annotations

import contextlib
import json
import shutil
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ..compression.ledger import (
    append_cost,
    reserve_cost,
    rollback_reservation,
    settle_reservation,
)
from ..privacy import redact
from ..temporal.graphiti_export import group_id_for_scope
from .episode_stage import KgConfig

KG_LEDGER_KIND = "kg_add_episode"
MAX_QUEUE_LINE_BYTES = 1_048_576
MAX_CORRUPTION_DETAILS = 32


@dataclass(frozen=True)
class _QueueRead:
    events: list[dict[str, Any]]
    corrupt_line: int | None = None
    corrupt_reason: str | None = None
    read_error: bool = False


def _log(config: KgConfig, record: dict[str, Any]) -> None:
    try:
        config.worker_log.parent.mkdir(parents=True, exist_ok=True)
        with config.worker_log.open("a", encoding="utf-8") as fp:
            fp.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
    except OSError:
        pass


def _find_queue_files(config: KgConfig) -> list[Path]:
    host_dir = config.queue_dir / config.host
    if not host_dir.exists():
        return []
    return sorted(host_dir.rglob("*-events.jsonl"))


def _read_events(queue_file: Path) -> _QueueRead:
    events: list[dict[str, Any]] = []
    try:
        with queue_file.open("rb") as fp:
            line_number = 0
            while True:
                raw_line = fp.readline(MAX_QUEUE_LINE_BYTES + 1)
                if not raw_line:
                    break
                line_number += 1
                if not raw_line.strip():
                    continue
                if len(raw_line) > MAX_QUEUE_LINE_BYTES:
                    return _QueueRead(
                        events,
                        corrupt_line=line_number,
                        corrupt_reason="line_too_large",
                    )
                if not raw_line.endswith(b"\n"):
                    return _QueueRead(
                        events,
                        corrupt_line=line_number,
                        corrupt_reason="unterminated_line",
                    )
                try:
                    line = raw_line.decode("utf-8").strip()
                except UnicodeDecodeError:
                    return _QueueRead(
                        events,
                        corrupt_line=line_number,
                        corrupt_reason="invalid_utf8",
                    )
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    return _QueueRead(
                        events,
                        corrupt_line=line_number,
                        corrupt_reason="invalid_json",
                    )
                if not isinstance(event, dict):
                    return _QueueRead(
                        events,
                        corrupt_line=line_number,
                        corrupt_reason="record_not_object",
                    )
                events.append(event)
    except (OSError, UnicodeError):
        return _QueueRead(events, read_error=True)
    return _QueueRead(events)


def _archive_file(queue_file: Path, config: KgConfig) -> None:
    try:
        rel = queue_file.relative_to(config.queue_dir)
    except ValueError:
        return
    dest = config.processed_dir / rel
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        queue_file.replace(dest)
    except OSError:
        # Cross-device move (EXDEV): fall back to copy-then-unlink so the
        # drain loop can continue rather than aborting on a tmpfs/NFS edge case.
        try:
            shutil.copy2(str(queue_file), str(dest))
            queue_file.unlink()
        except OSError:
            return


def _episode_text(event: dict[str, Any]) -> str:
    payload = event.get("payload", {}) or {}
    tool_name = payload.get("tool_name", "unknown_tool")
    tool_input = payload.get("tool_input", {})
    tool_response = payload.get("tool_response", {})
    parts = [f"Tool: {tool_name}"]
    if tool_input:
        parts.append(
            "Input: "
            + json.dumps(tool_input, ensure_ascii=False, default=str)[:1500]
        )
    if tool_response:
        parts.append(
            "Response: "
            + json.dumps(tool_response, ensure_ascii=False, default=str)[:1500]
        )
    return "\n".join(parts)


def drain_dry_run(config: KgConfig) -> dict[str, Any]:
    """Parse the queue and report counts. No LLM, no Neo4j connection."""
    files = _find_queue_files(config)
    total_events = 0
    seen_hashes: set[str] = set()
    corrupt_files = 0
    corrupt_records = 0
    read_errors = 0
    corrupt_details: list[dict[str, object]] = []
    for f in files:
        parsed = _read_events(f)
        total_events += len(parsed.events)
        if parsed.read_error:
            read_errors += 1
            if len(corrupt_details) < MAX_CORRUPTION_DETAILS:
                corrupt_details.append({"file": f.name, "reason": "read_error"})
        if parsed.corrupt_line is not None:
            corrupt_files += 1
            corrupt_records += 1
            if len(corrupt_details) < MAX_CORRUPTION_DETAILS:
                corrupt_details.append(
                    {
                        "file": f.name,
                        "line": parsed.corrupt_line,
                        "reason": parsed.corrupt_reason or "corrupt_record",
                    }
                )
        for e in parsed.events:
            h = e.get("content_hash")
            if h:
                seen_hashes.add(h)
    result = {
        "mode": "dry-run",
        "files_found": len(files),
        "total_events": total_events,
        "unique_events_by_hash": len(seen_hashes),
        "corrupt_files": corrupt_files,
        "corrupt_records": corrupt_records,
        "read_errors": read_errors,
        "corrupt_details": corrupt_details,
        "host": config.host,
    }
    _log(config, {"ts": datetime.now(UTC).isoformat(), **result})
    return result


def _load_credentials(config: KgConfig) -> dict[str, str]:
    if not config.credentials_path.is_file():
        raise RuntimeError(f"Credentials not found at {config.credentials_path}")
    data: dict[str, str] = json.loads(
        config.credentials_path.read_text(encoding="utf-8")
    )
    return data


def _record_episode_cost(
    config: KgConfig, usd: float, reservation_id: str | None
) -> None:
    """Settle a reservation, or append an audit record when uncapped.

    With a cap set, every episode passed through ``reserve_cost`` first;
    settle it to the actual cost here. With no cap, no reservation was
    taken but spend is still recorded so the operator's bill audit is
    complete. Disk and lock errors are swallowed: a lingering
    reservation only over-counts the cap, which is the conservative
    direction.
    """
    try:
        if reservation_id is not None:
            settle_reservation(
                config.cost_ledger,
                reservation_id,
                actual_cost_usd=usd,
                model="graphiti",
                tokens_in=0,
                tokens_out=0,
                host=config.host,
                kind=KG_LEDGER_KIND,
            )
        else:
            append_cost(
                config.cost_ledger,
                model="graphiti",
                tokens_in=0,
                tokens_out=0,
                cost_usd=usd,
                host=config.host,
                kind=KG_LEDGER_KIND,
            )
    except (OSError, TimeoutError):
        pass


def _release_reservation(config: KgConfig, reservation_id: str | None) -> None:
    """Roll back a reservation after a provider failure. Best-effort."""
    if reservation_id is None:
        return
    with contextlib.suppress(OSError, TimeoutError):
        rollback_reservation(config.cost_ledger, reservation_id, kind=KG_LEDGER_KIND)


def drain_live(
    config: KgConfig, *, per_episode_usd_estimate: float = 0.002
) -> dict[str, Any]:
    """Drain the queue into Graphiti. Lazily imports graphiti_core.

    The ``per_episode_usd_estimate`` is a rough per-add cost used for
    cap enforcement. The real bill comes from the LLM provider; the
    operator can reconcile against ``cost_ledger`` at month end.
    """
    try:
        from graphiti_core import Graphiti  # noqa: WPS433
        from graphiti_core.nodes import EpisodeType  # noqa: WPS433
    except ImportError:
        return {"error": "graphiti_core not installed. Install with mneme[full]."}

    try:
        creds = _load_credentials(config)
    except (RuntimeError, OSError, json.JSONDecodeError) as exc:
        return {"error": f"credentials unreadable: {exc}"}

    try:
        client = Graphiti(creds["bolt_url"], creds["user"], creds["password"])
    except Exception as exc:  # noqa: BLE001 - third-party surface
        return {"error": f"Neo4j connection failed: {exc}"}

    files = _find_queue_files(config)
    added = 0
    failed = 0
    skipped_by_cap = 0
    corrupt_files = 0
    corrupt_records = 0
    read_errors = 0
    corrupt_details: list[dict[str, object]] = []
    cap = config.cost_cap_usd_monthly

    for f in files:
        # Per-file counters: archive only when THIS file's episodes all
        # succeeded so a failing file does not prevent archiving clean files.
        file_added = 0
        file_failed = 0
        parsed = _read_events(f)
        if parsed.read_error or parsed.corrupt_line is not None:
            if parsed.read_error:
                read_errors += 1
                if len(corrupt_details) < MAX_CORRUPTION_DETAILS:
                    corrupt_details.append({"file": f.name, "reason": "read_error"})
            if parsed.corrupt_line is not None:
                corrupt_files += 1
                corrupt_records += 1
                if len(corrupt_details) < MAX_CORRUPTION_DETAILS:
                    corrupt_details.append(
                        {
                            "file": f.name,
                            "line": parsed.corrupt_line,
                            "reason": parsed.corrupt_reason or "corrupt_record",
                        }
                    )
            continue
        for e in parsed.events:
            reservation_id: str | None = None
            if cap is not None:
                try:
                    reservation_id, ok = reserve_cost(
                        config.cost_ledger,
                        estimated_cost_usd=per_episode_usd_estimate,
                        host=config.host,
                        cap_usd_monthly=cap,
                        kind=KG_LEDGER_KIND,
                    )
                except (OSError, TimeoutError):
                    # Fail-closed: could not take a reservation, so do not
                    # spend. Count as cap-skipped and leave the file for a
                    # later drain once the ledger is writable again.
                    skipped_by_cap += 1
                    continue
                if not ok:
                    skipped_by_cap += 1
                    continue
            ts = e.get("ts") or datetime.now(UTC).isoformat()
            try:
                ref_time = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
            except ValueError:
                ref_time = datetime.now(UTC)
            try:
                import asyncio  # noqa: WPS433 - keep CLI startup cheap

                # Queue files are derived state and may have been modified after
                # staging. Redact again at the final provider boundary.
                provider_name = redact(str(e.get("event_id", "evt"))) or "evt"
                provider_body = redact(_episode_text(e))
                group_id = group_id_for_scope(str(e.get("scope") or "default"))
                asyncio.run(
                    client.add_episode(
                        name=provider_name,
                        episode_body=provider_body,
                        source=EpisodeType.text,
                        source_description="mneme PostToolUse stage",
                        reference_time=ref_time,
                        group_id=group_id,
                    )
                )
                file_added += 1
                added += 1
                _record_episode_cost(config, per_episode_usd_estimate, reservation_id)
            except Exception:  # noqa: BLE001 - third-party surface
                file_failed += 1
                failed += 1
                _release_reservation(config, reservation_id)
        # Archive only when all episodes in this specific file succeeded.
        if file_added > 0 and file_failed == 0:
            _archive_file(f, config)

    result = {
        "mode": "live",
        "files_processed": len(files),
        "episodes_added": added,
        "episodes_failed": failed,
        "skipped_by_cost_cap": skipped_by_cap,
        "corrupt_files": corrupt_files,
        "corrupt_records": corrupt_records,
        "read_errors": read_errors,
        "corrupt_details": corrupt_details,
        "host": config.host,
    }
    _log(config, {"ts": datetime.now(UTC).isoformat(), **result})
    return result


def community_refresh(config: KgConfig) -> dict[str, Any]:
    """Run Graphiti.build_communities() once and clear the refresh flag."""
    try:
        from graphiti_core import Graphiti  # noqa: WPS433
    except ImportError:
        return {"error": "graphiti_core not installed. Install with mneme[full]."}

    try:
        creds = _load_credentials(config)
    except (RuntimeError, OSError, json.JSONDecodeError) as exc:
        return {"error": f"credentials unreadable: {exc}"}

    try:
        client = Graphiti(creds["bolt_url"], creds["user"], creds["password"])
        import asyncio  # noqa: WPS433

        asyncio.run(client.build_communities())
    except Exception as exc:  # noqa: BLE001
        return {"error": f"community refresh failed: {exc}"}

    try:
        if config.community_refresh_flag.exists():
            config.community_refresh_flag.unlink()
    except OSError:
        pass

    result = {
        "status": "ok",
        "refreshed_at": datetime.now(UTC).isoformat(),
    }
    _log(config, result)
    return result
