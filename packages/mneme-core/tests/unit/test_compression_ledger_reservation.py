"""Tests for the Codex Pass 2 reservation pattern in the cost ledger.

Five behaviors land here:

  1. ``reserve_cost`` writes a reservation record when spend + estimate
     stays below the cap, and reports ``ok=True`` plus the id.
  2. ``reserve_cost`` refuses (``ok=False``) when the reservation
     would push effective spend to or above the cap, and writes
     nothing.
  3. ``month_to_date_spend`` counts reservations toward the same
     ``kind`` so a second concurrent caller sees the in-flight spend
     and respects the budget.
  4. ``settle_reservation`` removes the matching reservation line and
     appends a settled-cost line at the actual cost.
  5. ``rollback_reservation`` removes the reservation without
     appending anything.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from mneme_core.compression.ledger import (
    DEFAULT_LEDGER_KIND,
    RESERVATION_KIND_SUFFIX,
    append_cost,
    month_to_date_spend,
    reserve_cost,
    rollback_reservation,
    settle_reservation,
)


@pytest.fixture
def ledger_path(tmp_path: Path) -> Path:
    return tmp_path / "cost-ledger.jsonl"


def _read_records(ledger_path: Path) -> list[dict]:
    if not ledger_path.is_file():
        return []
    return [
        json.loads(line)
        for line in ledger_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


class TestReserveCost:
    def test_reserves_when_under_cap(self, ledger_path: Path) -> None:
        rid, ok = reserve_cost(
            ledger_path,
            estimated_cost_usd=0.50,
            host="test-host",
            cap_usd_monthly=10.0,
        )
        assert ok is True
        assert rid
        records = _read_records(ledger_path)
        assert len(records) == 1
        assert records[0]["kind"] == DEFAULT_LEDGER_KIND + RESERVATION_KIND_SUFFIX
        assert records[0]["reservation_id"] == rid
        assert records[0]["cost_usd"] == 0.50

    def test_refuses_when_reservation_breaches_cap(
        self, ledger_path: Path
    ) -> None:
        # Pre-stamp spend close to cap.
        append_cost(
            ledger_path,
            model="m",
            tokens_in=1,
            tokens_out=1,
            cost_usd=9.80,
            host="h",
        )
        rid, ok = reserve_cost(
            ledger_path,
            estimated_cost_usd=0.50,
            host="h",
            cap_usd_monthly=10.0,
        )
        assert ok is False
        # No reservation record written.
        records = _read_records(ledger_path)
        assert not any(
            r.get("reservation_id") == rid for r in records
        )

    def test_reservations_count_toward_concurrent_cap_math(
        self, ledger_path: Path
    ) -> None:
        # First worker reserves 8.0 against a 10.0 cap.
        rid1, ok1 = reserve_cost(
            ledger_path,
            estimated_cost_usd=8.0,
            host="w1",
            cap_usd_monthly=10.0,
        )
        assert ok1 is True
        # Second worker tries to reserve 3.0: total would be 11.0, refused.
        _, ok2 = reserve_cost(
            ledger_path,
            estimated_cost_usd=3.0,
            host="w2",
            cap_usd_monthly=10.0,
        )
        assert ok2 is False
        # month_to_date_spend reflects the in-flight reservation.
        assert month_to_date_spend(ledger_path) >= 8.0


class TestSettleReservation:
    def test_replaces_reservation_with_actual_cost(
        self, ledger_path: Path
    ) -> None:
        rid, ok = reserve_cost(
            ledger_path,
            estimated_cost_usd=1.0,
            host="h",
            cap_usd_monthly=10.0,
        )
        assert ok
        settle_reservation(
            ledger_path,
            rid,
            actual_cost_usd=0.42,
            model="m",
            tokens_in=100,
            tokens_out=50,
            host="h",
        )
        records = _read_records(ledger_path)
        # Reservation is gone.
        assert not any(
            r.get("kind") == DEFAULT_LEDGER_KIND + RESERVATION_KIND_SUFFIX
            for r in records
        )
        # Settled cost is present.
        settled = [
            r
            for r in records
            if r.get("settled_from_reservation") == rid
        ]
        assert len(settled) == 1
        assert settled[0]["cost_usd"] == 0.42
        # Effective spend now reflects actual, not estimate.
        assert month_to_date_spend(ledger_path) == pytest.approx(0.42)

    def test_settle_with_no_matching_reservation_still_appends_cost(
        self, ledger_path: Path
    ) -> None:
        settle_reservation(
            ledger_path,
            "ghost-id",
            actual_cost_usd=0.10,
            model="m",
            tokens_in=10,
            tokens_out=5,
            host="h",
        )
        records = _read_records(ledger_path)
        assert len(records) == 1
        assert records[0]["cost_usd"] == 0.10


class TestRollbackReservation:
    def test_drops_reservation_on_rollback(self, ledger_path: Path) -> None:
        rid, ok = reserve_cost(
            ledger_path,
            estimated_cost_usd=2.0,
            host="h",
            cap_usd_monthly=10.0,
        )
        assert ok
        rollback_reservation(ledger_path, rid)
        records = _read_records(ledger_path)
        assert records == []
        # Spend drops back to zero so the cap headroom recovers.
        assert month_to_date_spend(ledger_path) == 0.0

    def test_rollback_of_missing_id_leaves_other_records_intact(
        self, ledger_path: Path
    ) -> None:
        # Pre-seed with an unrelated settled record.
        append_cost(
            ledger_path,
            model="m",
            tokens_in=1,
            tokens_out=1,
            cost_usd=0.05,
            host="h",
        )
        rollback_reservation(ledger_path, "no-such-id")
        records = _read_records(ledger_path)
        assert len(records) == 1
        assert records[0]["cost_usd"] == 0.05
