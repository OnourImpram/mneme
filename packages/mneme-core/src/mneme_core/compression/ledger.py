"""Cost ledger for opt-in LLM compression.

The ledger is a JSONL file (one record per LLM call) co-located with
the vault's KG cost ledger (``vault.kg_cost_ledger``). Phase E and
Phase F share the same file so the operator gets a single bill view.

Public API:

- ``append_cost(ledger_path, *, model, tokens_in, tokens_out, cost_usd, host, kind)``
- ``month_to_date_spend(ledger_path, *, kind=None)``
- ``check_cap(ledger_path, *, cap_usd_monthly, pause_flag_path, kind=None)``
- ``reserve_cost(ledger_path, *, estimated_cost_usd, host, cap_usd_monthly)``
- ``settle_reservation(ledger_path, reservation_id, *, actual_cost_usd, ...)``
- ``rollback_reservation(ledger_path, reservation_id)``

The reservation API was added in the Codex Pass 2 review fix to
close the check-then-spend race. Multiple concurrent workers used to
each pass ``check_cap`` and then each charge the provider, so total
spend could pass the cap. Now every worker takes a reservation
record before the provider call. The reservation counts toward the
cap until either settled to the actual cost (success) or rolled
back (provider failure). A crashed worker leaves a reservation that
future cap math still respects, which is the conservative failure
mode (over-throttle, never over-spend).

Fail-closed semantics: reservation writes raise ``OSError`` so the
caller knows nothing was reserved. ``check_cap`` and ``append_cost``
keep their original soft-fail semantics for backwards compatibility.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from ..vault.atomic_write import atomic_write_text
from ..vault.file_lock import file_lock

DEFAULT_LEDGER_KIND = "compression"
RESERVATION_KIND_SUFFIX = "-reserved"
_LOCK_TIMEOUT_S = 5.0


class LedgerCorruptError(Exception):
    """Raised when the ledger file exists but contains no parseable records.

    This is the fail-closed sentinel: a corrupt ledger must not be silently
    treated as empty (zero spend), which would let compression proceed past
    the cost cap. Callers that catch this should block the operation and
    surface the error to the operator for manual inspection.
    """


@dataclass(frozen=True)
class CapCheck:
    """Outcome of a cap evaluation against the ledger."""

    over_cap: bool
    spend_usd: float
    cap_usd: float
    pause_flag_written: bool


def append_cost(
    ledger_path: Path,
    *,
    model: str,
    tokens_in: int,
    tokens_out: int,
    cost_usd: float,
    host: str,
    kind: str = DEFAULT_LEDGER_KIND,
) -> None:
    """Atomically append one cost record. Never raises.

    The append runs under the same sidecar lock the reservation triad
    (``reserve_cost`` / ``settle_reservation`` / ``rollback_reservation``)
    holds. Without it, an unlocked append landing inside the read window
    of a locked settle/rollback rewrite was destroyed by the rewrite's
    ``os.replace`` — a committed charge would vanish and ``month_to_date_
    spend`` would under-count, the unsafe direction for a cost cap.

    Failure stays soft: disk errors (``OSError``) are suppressed inside
    the lock, and a lock-acquisition ``TimeoutError`` returns without
    raising, preserving the original "never raises" contract.
    """
    record: dict[str, Any] = {
        "ts": datetime.now(UTC).isoformat(),
        "kind": kind,
        "model": model,
        "tokens_in": int(tokens_in),
        "tokens_out": int(tokens_out),
        "cost_usd": round(float(cost_usd), 6),
        "usd": round(float(cost_usd), 6),
        "host": host,
    }
    try:
        with file_lock(_lock_path_for(ledger_path), timeout_s=_LOCK_TIMEOUT_S):
            try:
                ledger_path.parent.mkdir(parents=True, exist_ok=True)
                with ledger_path.open("a", encoding="utf-8") as fp:
                    fp.write(json.dumps(record, ensure_ascii=False) + "\n")
            except OSError:
                return
    except (OSError, TimeoutError):
        # OSError here can only come from the lockfile setup itself; a
        # TimeoutError means another worker held the lock past the deadline.
        # Both are soft per the never-raises contract.
        return


def _iter_records(ledger_path: Path) -> list[dict[str, Any]]:
    """Parse ledger records, distinguishing missing from corrupt.

    - File absent: return ``[]`` (0.0 spend is correct, proceed).
    - File present, at least one parseable record: return good records
      (partial corruption skips bad lines gracefully).
    - File present, has non-empty lines, but *zero* parse: raise
      ``LedgerCorruptError`` so callers fail closed rather than treating
      a corrupt file as an empty ledger (which would zero out spend and
      allow compression past the cap).
    """
    if not ledger_path.is_file():
        return []
    out: list[dict[str, Any]] = []
    nonempty_lines = 0
    try:
        with ledger_path.open("r", encoding="utf-8") as fp:
            for line in fp:
                line = line.strip()
                if not line:
                    continue
                nonempty_lines += 1
                try:
                    out.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    except OSError:
        return []
    if nonempty_lines > 0 and not out:
        raise LedgerCorruptError(
            f"Ledger file exists but contains no parseable records: {ledger_path}. "
            "Inspect the file manually, then remove or repair it before retrying."
        )
    return out


def month_to_date_spend(
    ledger_path: Path,
    *,
    kind: str | None = DEFAULT_LEDGER_KIND,
    now: datetime | None = None,
) -> float:
    """Sum of ``cost_usd`` (or ``usd``) for the current calendar month.

    When ``kind`` is ``None`` the sum spans every kind, which is useful
    for total bill audits. The default scopes to the compression kind
    so KG-add spend does not leak into compression-cap math.

    Reservations of the matching kind (``kind + "-reserved"``) are
    counted toward spend. This is the Codex Pass 2 reservation
    pattern: any in-flight worker has already debited the cap before
    the provider call, so concurrent workers see the reservation as
    spent and respect the budget.
    """
    records = _iter_records(ledger_path)
    if not records:
        return 0.0
    now = now or datetime.now(UTC)
    cur_month = now.strftime("%Y-%m")
    matching_kinds: set[str] | None = (
        None if kind is None else {kind, kind + RESERVATION_KIND_SUFFIX}
    )
    total = 0.0
    for rec in records:
        rec_kind = rec.get("kind")
        if matching_kinds is not None and rec_kind not in matching_kinds:
            continue
        ts = str(rec.get("ts", ""))
        if not ts.startswith(cur_month):
            continue
        try:
            total += float(rec.get("cost_usd", rec.get("usd", 0)))
        except (TypeError, ValueError):
            continue
    return total


def rolling_30d_spend(
    ledger_path: Path,
    *,
    kind: str | None = DEFAULT_LEDGER_KIND,
    now: datetime | None = None,
) -> float:
    """Sum of cost over the last 30 calendar days. Same kind semantics."""
    records = _iter_records(ledger_path)
    if not records:
        return 0.0
    now = now or datetime.now(UTC)
    cutoff = now - timedelta(days=30)
    matching_kinds: set[str] | None = (
        None if kind is None else {kind, kind + RESERVATION_KIND_SUFFIX}
    )
    total = 0.0
    for rec in records:
        if matching_kinds is not None and rec.get("kind") not in matching_kinds:
            continue
        ts = str(rec.get("ts", ""))
        try:
            record_ts = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        except ValueError:
            continue
        if record_ts < cutoff:
            continue
        try:
            total += float(rec.get("cost_usd", rec.get("usd", 0)))
        except (TypeError, ValueError):
            continue
    return total


def _lock_path_for(ledger_path: Path) -> Path:
    """Sidecar lockfile path used by the reservation primitives."""
    return ledger_path.with_suffix(ledger_path.suffix + ".lock")


def _rewrite_ledger_atomic(
    ledger_path: Path, kept_lines: list[str], appended: dict[str, Any] | None
) -> None:
    """Atomically replace the ledger contents with a durable write.

    Delegates to :func:`mneme_core.vault.atomic_write.atomic_write_text`,
    which fsyncs the file descriptor and (on POSIX) the parent directory
    before/after the rename. The plain ``os.replace`` this used to do was
    atomic but not durable: a crash after the rename but before the page
    cache flushed could still lose just-settled records. Callers hold the
    ledger lock, so the read-modify-write is already serialized.
    """
    payload_lines = list(kept_lines)
    if appended is not None:
        payload_lines.append(json.dumps(appended, ensure_ascii=False))
    content = "\n".join(payload_lines)
    if content:
        content += "\n"
    atomic_write_text(ledger_path, content)


def reserve_cost(
    ledger_path: Path,
    *,
    estimated_cost_usd: float,
    host: str,
    cap_usd_monthly: float,
    kind: str = DEFAULT_LEDGER_KIND,
    now: datetime | None = None,
) -> tuple[str, bool]:
    """Reserve ``estimated_cost_usd`` against the monthly cap.

    Returns ``(reservation_id, ok)``. When ``ok`` is False the
    reservation would push spend at or above the cap and the caller
    must abort the provider call. The reservation record is *not*
    written in that case, so future workers see the same cap math.

    When ``ok`` is True the reservation is appended to the ledger
    and counts toward the cap (via ``month_to_date_spend``) until
    ``settle_reservation`` or ``rollback_reservation`` is called.

    Reservation, check, and append all happen under a shared lock
    against ``ledger_path + ".lock"`` so two workers cannot pass
    the same cap check and both spend.

    Fail-closed: write errors raise ``OSError`` so the caller knows
    nothing was reserved.
    """
    reservation_id = str(uuid.uuid4())
    now = now or datetime.now(UTC)
    with file_lock(_lock_path_for(ledger_path), timeout_s=_LOCK_TIMEOUT_S):
        try:
            spend = month_to_date_spend(ledger_path, kind=kind, now=now)
        except LedgerCorruptError as exc:
            # Fail-closed: a corrupt ledger must not be treated as zero spend.
            # Re-raise as OSError so the pipeline's reservation_failed handler
            # surfaces the problem without allowing the provider call.
            raise OSError(f"ledger_corrupt: {exc}") from exc
        if spend + float(estimated_cost_usd) >= cap_usd_monthly:
            return reservation_id, False
        record: dict[str, Any] = {
            "ts": now.isoformat(),
            "kind": kind + RESERVATION_KIND_SUFFIX,
            "reservation_id": reservation_id,
            "estimated_cost_usd": round(float(estimated_cost_usd), 6),
            "cost_usd": round(float(estimated_cost_usd), 6),
            "host": host,
        }
        ledger_path.parent.mkdir(parents=True, exist_ok=True)
        # Route through the atomic rewrite path (read existing + append) so
        # the reservation is written with the same fsync-and-rename durability
        # guarantee as settle_reservation and rollback_reservation, closing the
        # crash window where a plain open("a") write survives neither fsync
        # nor rename.
        existing_lines = _read_lines_dropping_reservation(
            ledger_path,
            reservation_id="__nonexistent__",
            target_kind=kind + RESERVATION_KIND_SUFFIX,
        )
        _rewrite_ledger_atomic(ledger_path, existing_lines, appended=record)
    return reservation_id, True


def settle_reservation(
    ledger_path: Path,
    reservation_id: str,
    *,
    actual_cost_usd: float,
    model: str,
    tokens_in: int,
    tokens_out: int,
    host: str,
    kind: str = DEFAULT_LEDGER_KIND,
    now: datetime | None = None,
) -> None:
    """Replace a reservation with the actual settled cost.

    Drops the matching reservation line and appends a settled-cost
    line at the supplied actual cost. Lock serializes against
    concurrent reserve / settle / rollback. Missing reservation is
    not an error; the actual cost is still appended.
    """
    now = now or datetime.now(UTC)
    settled: dict[str, Any] = {
        "ts": now.isoformat(),
        "kind": kind,
        "model": model,
        "tokens_in": int(tokens_in),
        "tokens_out": int(tokens_out),
        "cost_usd": round(float(actual_cost_usd), 6),
        "usd": round(float(actual_cost_usd), 6),
        "host": host,
        "settled_from_reservation": reservation_id,
    }
    target_kind = kind + RESERVATION_KIND_SUFFIX
    with file_lock(_lock_path_for(ledger_path), timeout_s=_LOCK_TIMEOUT_S):
        kept = _read_lines_dropping_reservation(
            ledger_path, reservation_id=reservation_id, target_kind=target_kind
        )
        _rewrite_ledger_atomic(ledger_path, kept, appended=settled)


def rollback_reservation(
    ledger_path: Path,
    reservation_id: str,
    *,
    kind: str = DEFAULT_LEDGER_KIND,
) -> None:
    """Drop a reservation without settling. Used on provider failure.

    A missed rollback is acceptable: the reservation lingers until the
    next month rolls over and continues to occupy cap headroom. That
    is the conservative direction; the operator may notice through
    the audit CLI and clean up manually.
    """
    target_kind = kind + RESERVATION_KIND_SUFFIX
    with file_lock(_lock_path_for(ledger_path), timeout_s=_LOCK_TIMEOUT_S):
        kept = _read_lines_dropping_reservation(
            ledger_path, reservation_id=reservation_id, target_kind=target_kind
        )
        _rewrite_ledger_atomic(ledger_path, kept, appended=None)


def _read_lines_dropping_reservation(
    ledger_path: Path, *, reservation_id: str, target_kind: str
) -> list[str]:
    """Return ledger lines with the named reservation removed."""
    if not ledger_path.is_file():
        return []
    kept: list[str] = []
    try:
        with ledger_path.open("r", encoding="utf-8") as fp:
            for raw in fp:
                line = raw.rstrip("\n")
                if not line.strip():
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    kept.append(line)
                    continue
                if (
                    isinstance(rec, dict)
                    and rec.get("kind") == target_kind
                    and rec.get("reservation_id") == reservation_id
                ):
                    continue
                kept.append(line)
    except OSError:
        return []
    return kept


def check_cap(
    ledger_path: Path,
    *,
    cap_usd_monthly: float,
    pause_flag_path: Path,
    kind: str | None = DEFAULT_LEDGER_KIND,
    now: datetime | None = None,
) -> CapCheck:
    """Evaluate month-to-date spend against the cap.

    When the cap is breached, write the pause flag with a structured
    reason. The flag's presence is the canonical signal to other
    pipeline entrypoints that compression should not run.
    """
    spend = month_to_date_spend(ledger_path, kind=kind, now=now)
    if spend < cap_usd_monthly:
        return CapCheck(
            over_cap=False,
            spend_usd=spend,
            cap_usd=cap_usd_monthly,
            pause_flag_written=False,
        )
    written = False
    try:
        pause_flag_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "paused_at": (now or datetime.now(UTC)).isoformat(),
            "cap_usd_monthly": cap_usd_monthly,
            "spend_usd": round(spend, 6),
            "reason": "cost_cap_exceeded",
        }
        pause_flag_path.write_text(
            json.dumps(payload, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        written = True
    except OSError:
        written = False
    return CapCheck(
        over_cap=True,
        spend_usd=spend,
        cap_usd=cap_usd_monthly,
        pause_flag_written=written,
    )
