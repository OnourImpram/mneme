"""Background AI compression pipeline.

The orchestrator that pulls staged events, asks the LLM provider to
compress them into vault markdown, writes the result to the daily
log, and stamps the cost ledger. None of this runs on the critical
path: the Stop hook stays deterministic and Zero-LLM.

Triggers (chosen by the operator, not the pipeline):

* ``mneme compress run`` CLI invocation.
* Scheduled task (cron, Windows Task Scheduler).
* SessionEnd-driven background spawn that returns immediately.

Sacred-constraint enforcement:

1. ``CompressionConfig.enabled == True`` is mandatory. The CLI is
   the only writer of that flag.
2. The pause flag (``compression-paused.flag``) is checked first.
3. The cost-cap ledger is consulted next; breach writes the pause
   flag and aborts.
4. The LLM provider call is the only network call in the path.
5. On any unexpected error, the pipeline returns a structured
   report. It never raises so a scheduled task does not page the
   operator.

This module is provider-agnostic. Callers inject an ``LlmProvider``;
the default Anthropic implementation lives in ``llm.py``.
"""

from __future__ import annotations

import importlib.resources
import json
import socket
import time
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from ..vault.atomic_write import atomic_write_text
from .config import CompressionConfig
from .ledger import (
    append_cost,
    check_cap,
    reserve_cost,
    rollback_reservation,
    settle_reservation,
)
from .llm import (
    DEFAULT_PRICING_PER_MTOK,
    AnthropicProvider,
    CompressionResult,
    LlmCallSpec,
    LlmProvider,
    LlmProviderError,
    cost_for_usage,
)

DEFAULT_DAILY_LOG_DIR_NAME = "sessions"
DEFAULT_OBSERVATION_MARKER = "## Auto-captured observations"
DEFAULT_PROMPT_RESOURCE = "compress-en.md"
STAGING_ACTIVE_WINDOW_S: float = 60.0
"""Compression skips staging files modified within this many seconds.
PostToolUse writes per-minute files, so a 60-second cutoff guarantees
the file's minute has rolled over before we read and archive it. This
closes the load-then-archive race documented in the Phase J Codex
review."""


def _short_hostname() -> str:
    return socket.gethostname().split(".")[0]


def _today_iso() -> str:
    return date.today().isoformat()


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class PipelineConfig:
    """Bundle of vault-derived paths plus operator config.

    Held separate from ``CompressionConfig`` so the orchestrator can
    receive both without forcing the config layer to know about file
    paths.
    """

    compression: CompressionConfig
    staging_dir: Path
    daily_log_dir: Path
    ledger_path: Path
    pause_flag_path: Path
    daily_log_marker: str = DEFAULT_OBSERVATION_MARKER
    host: str = field(default_factory=_short_hostname)
    prompt_path: Path | None = None
    staging_active_window_s: float = STAGING_ACTIVE_WINDOW_S
    """Skip staging files whose mtime is within this many seconds of now,
    so a concurrent PostToolUse append cannot be archived before
    compression sees it. Tests override to ``0`` to bypass the race
    guard when seeding events synchronously."""


@dataclass
class RunReport:
    """Structured outcome of one pipeline invocation. Never raises out."""

    status: str
    events_loaded: int = 0
    files_processed: int = 0
    tokens_in: int = 0
    tokens_out: int = 0
    cost_usd: float = 0.0
    written_path: str | None = None
    reason: str | None = None


def load_prompt(path: Path | None = None) -> str:
    """Load the compression prompt. Falls back to the bundled resource."""
    if path is not None and path.is_file():
        return path.read_text(encoding="utf-8")
    try:
        files = importlib.resources.files(
            "mneme_core.compression"
        ) / "prompts" / DEFAULT_PROMPT_RESOURCE
        return files.read_text(encoding="utf-8")
    except (OSError, ModuleNotFoundError, FileNotFoundError):
        return ""


def _is_paused(path: Path) -> bool:
    try:
        return path.is_file()
    except OSError:
        return False


def _load_events_from_staging(
    staging_dir: Path,
    host: str,
    active_window_s: float = STAGING_ACTIVE_WINDOW_S,
) -> list[tuple[dict[str, Any], Path]]:
    """Read JSONL lines under ``staging/{host}/`` from files past the cutoff.

    Files whose mtime is within ``active_window_s`` of now are skipped
    so a concurrent PostToolUse append cannot be archived without ever
    being compressed.
    """
    host_root = staging_dir / host
    if not host_root.exists():
        return []
    cutoff = time.time() - active_window_s
    out: list[tuple[dict[str, Any], Path]] = []
    for jsonl_path in host_root.rglob("*.jsonl"):
        if "archive" in jsonl_path.parts:
            continue
        try:
            mtime = jsonl_path.stat().st_mtime
            if mtime > cutoff:
                continue
            text = jsonl_path.read_text(encoding="utf-8")
        except OSError:
            continue
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(event, dict):
                continue
            out.append((event, jsonl_path))
    return out


def _archive_files(files: Iterable[Path], staging_dir: Path) -> None:
    archive_root = staging_dir / "archive"
    try:
        archive_root.mkdir(parents=True, exist_ok=True)
    except OSError:
        return
    seen: set[Path] = set()
    for src in files:
        if src in seen:
            continue
        seen.add(src)
        try:
            if not src.exists() or "archive" in src.parts:
                continue
            relative = src.relative_to(staging_dir)
            dest = archive_root / relative
            dest.parent.mkdir(parents=True, exist_ok=True)
            src.replace(dest)
        except (OSError, ValueError):
            continue


def _extract_content_hashes(markdown: str) -> set[str]:
    """Pull SHA256 hashes out of the LLM output for dedup."""
    hashes: set[str] = set()
    for line in markdown.splitlines():
        if "content_hash:" in line:
            value = line.split("content_hash:", 1)[1].strip().strip('"')
            if value:
                hashes.add(value)
    return hashes


def _existing_hashes_in(path: Path) -> set[str]:
    if not path.is_file():
        return set()
    try:
        return _extract_content_hashes(path.read_text(encoding="utf-8"))
    except OSError:
        return set()


def _append_to_daily_log(
    markdown: str, daily_path: Path, marker: str
) -> None:
    daily_path.parent.mkdir(parents=True, exist_ok=True)
    if daily_path.exists():
        existing = daily_path.read_text(encoding="utf-8")
    else:
        existing = f"# {_today_iso()}\n\n"
    if marker not in existing:
        existing = existing.rstrip() + f"\n\n{marker}\n\n"
    updated = existing.rstrip() + "\n\n" + markdown.strip() + "\n"
    atomic_write_text(daily_path, updated)


def _truncate_payload(payload: str, limit: int) -> str:
    if len(payload.encode("utf-8")) <= limit:
        return payload
    return payload[:limit] + "\n...[TRUNCATED]"


# Pricing-per-million-tokens lookup uses ``DEFAULT_PRICING_PER_MTOK`` from
# .llm with the per-model schema ``{"input": float, "output": float}``.
# The estimator computes a conservative upper bound on what one call
# could cost so the reservation covers any reasonable actual spend.
# Chars-per-token approximation matches the ``mneme audit`` heuristic.
_ESTIMATE_CHARS_PER_TOKEN = 4
_ESTIMATE_PRICE_FALLBACK: dict[str, float] = {"input": 4.0, "output": 20.0}


def _estimate_max_cost(config: PipelineConfig, payload: str) -> float:
    """Conservative upper-bound dollar cost for one provider call.

    Used by ``reserve_cost`` so the cap math reflects in-flight calls
    before the provider returns. Over-estimating is safer than
    under-estimating; the actual cost from ``cost_for_usage`` settles
    the reservation on success.
    """
    estimated_tokens_in = max(
        1, len(payload) // _ESTIMATE_CHARS_PER_TOKEN
    )
    estimated_tokens_out = max(1, int(config.compression.max_tokens))
    pricing = DEFAULT_PRICING_PER_MTOK.get(
        config.compression.model, _ESTIMATE_PRICE_FALLBACK
    )
    price_in = float(pricing.get("input", _ESTIMATE_PRICE_FALLBACK["input"]))
    price_out = float(pricing.get("output", _ESTIMATE_PRICE_FALLBACK["output"]))
    cost = (
        (estimated_tokens_in / 1_000_000) * price_in
        + (estimated_tokens_out / 1_000_000) * price_out
    )
    # Small safety multiplier (1.1x) so settlement never overshoots
    # the reservation by enough to backfill below zero on rounding.
    return round(cost * 1.1, 6)


def _try_rollback(ledger_path: Path, reservation_id: str) -> None:
    """Best-effort rollback. Pipeline must never raise out of itself."""
    try:
        rollback_reservation(ledger_path, reservation_id)
    except (OSError, TimeoutError):
        # A missed rollback leaves the reservation in place. The
        # operator's cap headroom shrinks until next-month rollover or
        # manual cleanup; the audit CLI surfaces orphaned reservations.
        return


def _try_settle(
    ledger_path: Path,
    reservation_id: str,
    *,
    actual_cost_usd: float,
    model: str,
    tokens_in: int,
    tokens_out: int,
    host: str,
) -> None:
    """Best-effort settle. Falls back to append on lock/IO error."""
    try:
        settle_reservation(
            ledger_path,
            reservation_id,
            actual_cost_usd=actual_cost_usd,
            model=model,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            host=host,
        )
    except (OSError, TimeoutError):
        # Settlement failed; the reservation stays as it is so the cap
        # math still respects the in-flight spend. Append the actual
        # cost so the operator's bill view is complete.
        append_cost(
            ledger_path,
            model=model,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            cost_usd=actual_cost_usd,
            host=host,
        )


def run_compression(
    config: PipelineConfig,
    provider: LlmProvider | None = None,
    *,
    dry_run: bool = False,
) -> RunReport:
    """End-to-end pipeline pass. Returns a structured outcome.

    The function never raises. Operators run this on a scheduler and
    expect a benign report even when nothing is staged.
    """
    if not config.compression.enabled:
        return RunReport(status="disabled", reason="compression_enabled=false")

    if _is_paused(config.pause_flag_path):
        return RunReport(status="paused", reason="pause_flag_present")

    events_with_paths = _load_events_from_staging(
        config.staging_dir,
        config.host,
        active_window_s=config.staging_active_window_s,
    )
    if not events_with_paths:
        return RunReport(status="empty", reason="no_staging_events")

    events = [e for e, _ in events_with_paths]
    source_files = [p for _, p in events_with_paths]

    if dry_run:
        return RunReport(
            status="dry-run",
            events_loaded=len(events),
            files_processed=len({p for p in source_files}),
        )

    # Codex Pass 2 reservation pattern: reserve an estimated max-cost
    # against the monthly cap *before* the provider call. The
    # reservation counts toward the cap until settled (success) or
    # rolled back (failure). Two workers can no longer both pass a
    # naive check and both spend.
    prompt = load_prompt(config.prompt_path)
    if not prompt.strip():
        return RunReport(status="error", reason="compression_prompt_unavailable")

    payload = json.dumps(events, ensure_ascii=False, default=str)
    payload = _truncate_payload(payload, config.compression.max_payload_bytes)

    estimated_cost = _estimate_max_cost(config, payload)
    try:
        reservation_id, reserved = reserve_cost(
            config.ledger_path,
            estimated_cost_usd=estimated_cost,
            host=config.host,
            cap_usd_monthly=config.compression.cost_cap_usd_monthly,
        )
    except OSError as exc:
        # Fail-closed: reservation could not be written, so we cannot
        # safely charge the provider. Surface a write_failed status
        # rather than racing the cap.
        return RunReport(
            status="reservation_failed",
            reason=f"ledger_reservation_write_failed: {exc}",
        )
    if not reserved:
        # Mirror the legacy check_cap behavior: stamp the pause flag
        # so other entrypoints honor the breach.
        cap = check_cap(
            config.ledger_path,
            cap_usd_monthly=config.compression.cost_cap_usd_monthly,
            pause_flag_path=config.pause_flag_path,
        )
        return RunReport(
            status="cost_cap_exceeded",
            reason=(
                f"reservation refused, month-to-date spend "
                f"${cap.spend_usd:.4f} + estimate ${estimated_cost:.4f} "
                f">= cap ${cap.cap_usd:.4f}"
            ),
        )

    real_provider: LlmProvider = provider or AnthropicProvider()
    spec = LlmCallSpec(
        system=prompt,
        payload=payload,
        model=config.compression.model,
        max_tokens=config.compression.max_tokens,
    )
    try:
        result = real_provider.compress(spec)
    except LlmProviderError as exc:
        _try_rollback(config.ledger_path, reservation_id)
        return RunReport(status="provider_error", reason=str(exc))
    except Exception as exc:  # noqa: BLE001 - keep pipeline non-raising
        _try_rollback(config.ledger_path, reservation_id)
        return RunReport(status="error", reason=f"provider_unexpected: {exc}")

    if not result.text.strip():
        # No tokens billed in the empty-output case, but we still
        # release the reservation so the cap headroom recovers.
        _try_rollback(config.ledger_path, reservation_id)
        return RunReport(
            status="empty_output",
            events_loaded=len(events),
            tokens_in=result.tokens_in,
            tokens_out=result.tokens_out,
        )

    daily_path = config.daily_log_dir / f"{_today_iso()}.md"
    existing = _existing_hashes_in(daily_path)
    new_hashes = _extract_content_hashes(result.text)
    if new_hashes and new_hashes.issubset(existing):
        # Duplicate output, no daily-log write. The provider call did
        # happen, so we settle to actual cost rather than rolling back.
        actual_dup_cost = cost_for_usage(
            tokens_in=result.tokens_in,
            tokens_out=result.tokens_out,
            model=result.model,
            pricing_per_mtok=DEFAULT_PRICING_PER_MTOK,
        )
        _try_settle(
            config.ledger_path,
            reservation_id,
            actual_cost_usd=actual_dup_cost,
            model=result.model,
            tokens_in=result.tokens_in,
            tokens_out=result.tokens_out,
            host=config.host,
        )
        return RunReport(
            status="duplicate",
            events_loaded=len(events),
            tokens_in=result.tokens_in,
            tokens_out=result.tokens_out,
            cost_usd=round(actual_dup_cost, 6),
            reason="all_content_hashes_already_present",
        )

    try:
        _append_to_daily_log(
            result.text, daily_path, config.daily_log_marker
        )
    except OSError as exc:
        # Daily log write failed after the provider was charged.
        # Settle the reservation so the cap math reflects reality.
        actual_write_fail_cost = cost_for_usage(
            tokens_in=result.tokens_in,
            tokens_out=result.tokens_out,
            model=result.model,
            pricing_per_mtok=DEFAULT_PRICING_PER_MTOK,
        )
        _try_settle(
            config.ledger_path,
            reservation_id,
            actual_cost_usd=actual_write_fail_cost,
            model=result.model,
            tokens_in=result.tokens_in,
            tokens_out=result.tokens_out,
            host=config.host,
        )
        return RunReport(status="write_failed", reason=str(exc))

    _archive_files(source_files, config.staging_dir)

    cost = cost_for_usage(
        tokens_in=result.tokens_in,
        tokens_out=result.tokens_out,
        model=result.model,
        pricing_per_mtok=DEFAULT_PRICING_PER_MTOK,
    )
    _try_settle(
        config.ledger_path,
        reservation_id,
        actual_cost_usd=cost,
        model=result.model,
        tokens_in=result.tokens_in,
        tokens_out=result.tokens_out,
        host=config.host,
    )

    return RunReport(
        status="ok",
        events_loaded=len(events),
        files_processed=len({p for p in source_files}),
        tokens_in=result.tokens_in,
        tokens_out=result.tokens_out,
        cost_usd=round(cost, 6),
        written_path=str(daily_path),
    )


def pipeline_config_from_vault(
    vault: Any,
    compression: CompressionConfig | None = None,
) -> PipelineConfig:
    """Convenience factory mirroring ``kg_config_from_vault``.

    ``vault`` is typed as ``Any`` to dodge a circular import. In
    practice it is always a ``VaultConfig`` with the new compression
    paths added in Phase F.
    """
    cfg = compression or CompressionConfig.default()
    return PipelineConfig(
        compression=cfg,
        staging_dir=vault.staging_dir,
        daily_log_dir=vault.compression_daily_log_dir,
        ledger_path=vault.kg_cost_ledger,
        pause_flag_path=vault.compression_pause_flag,
    )
