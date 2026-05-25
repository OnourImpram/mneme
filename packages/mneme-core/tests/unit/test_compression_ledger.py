"""Tests for the Phase F cost ledger."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from mneme_core.compression.ledger import (
    CapCheck,
    LedgerCorruptError,
    append_cost,
    check_cap,
    month_to_date_spend,
    reserve_cost,
    rolling_30d_spend,
)


@pytest.fixture
def ledger_path(tmp_path: Path) -> Path:
    return tmp_path / "cost-ledger.jsonl"


@pytest.fixture
def pause_flag(tmp_path: Path) -> Path:
    return tmp_path / "compression-paused.flag"


class TestAppendCost:
    def test_writes_one_record_per_call(self, ledger_path: Path) -> None:
        append_cost(
            ledger_path,
            model="claude-sonnet-4-5",
            tokens_in=100,
            tokens_out=200,
            cost_usd=0.0033,
            host="testhost",
        )
        assert ledger_path.is_file()
        line = ledger_path.read_text(encoding="utf-8").strip().splitlines()[-1]
        rec = json.loads(line)
        assert rec["model"] == "claude-sonnet-4-5"
        assert rec["tokens_in"] == 100
        assert rec["tokens_out"] == 200
        assert rec["cost_usd"] == pytest.approx(0.0033)
        assert rec["usd"] == pytest.approx(0.0033)  # kg-compat alias
        assert rec["kind"] == "compression"
        assert rec["host"] == "testhost"

    def test_appends_without_clobber(self, ledger_path: Path) -> None:
        for i in range(3):
            append_cost(
                ledger_path,
                model="claude-haiku-4-5-20251001",
                tokens_in=i * 10,
                tokens_out=i * 5,
                cost_usd=0.001 * i,
                host="t",
            )
        lines = ledger_path.read_text(encoding="utf-8").strip().splitlines()
        assert len(lines) == 3

    def test_swallows_disk_errors(
        self, ledger_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def _boom(*_a: object, **_kw: object) -> None:
            raise OSError("disk full")

        monkeypatch.setattr(Path, "mkdir", _boom)
        # Must not raise. Returns None implicitly.
        append_cost(
            ledger_path,
            model="m",
            tokens_in=1,
            tokens_out=1,
            cost_usd=0.001,
            host="t",
        )


class TestMonthToDateSpend:
    def test_empty_ledger_zero(self, ledger_path: Path) -> None:
        assert month_to_date_spend(ledger_path) == 0.0

    def test_sums_only_current_month(self, ledger_path: Path) -> None:
        now = datetime(2026, 6, 15, tzinfo=UTC)
        cur = now.strftime("%Y-%m")
        records = [
            {"ts": "2025-12-30T12:00:00+00:00", "cost_usd": 99.0, "kind": "compression"},
            {"ts": f"{cur}-01T00:00:00+00:00", "cost_usd": 1.0, "kind": "compression"},
            {"ts": f"{cur}-10T00:00:00+00:00", "cost_usd": 2.5, "kind": "compression"},
            {"ts": f"{cur}-15T00:00:00+00:00", "cost_usd": 0.5, "kind": "kg_add_episode"},
        ]
        with ledger_path.open("w", encoding="utf-8") as fp:
            for r in records:
                fp.write(json.dumps(r) + "\n")
        # default kind filter excludes the KG entry
        assert month_to_date_spend(ledger_path, now=now) == pytest.approx(3.5)
        # kind=None sums everything in the month
        assert month_to_date_spend(ledger_path, kind=None, now=now) == pytest.approx(4.0)

    def test_falls_back_to_usd_field(self, ledger_path: Path) -> None:
        now = datetime(2026, 6, 15, tzinfo=UTC)
        cur = now.strftime("%Y-%m")
        # The Phase E kg ledger stamps "usd", not "cost_usd". Both should sum.
        record = {"ts": f"{cur}-05T00:00:00+00:00", "usd": 0.123, "kind": "compression"}
        with ledger_path.open("w", encoding="utf-8") as fp:
            fp.write(json.dumps(record) + "\n")
        assert month_to_date_spend(ledger_path, now=now) == pytest.approx(0.123)


class TestRolling30d:
    def test_drops_old_records(self, ledger_path: Path) -> None:
        now = datetime(2026, 6, 15, tzinfo=UTC)
        with ledger_path.open("w", encoding="utf-8") as fp:
            fp.write(json.dumps(
                {"ts": "2026-05-01T00:00:00+00:00", "cost_usd": 100.0, "kind": "compression"}
            ) + "\n")
            fp.write(json.dumps(
                {"ts": "2026-06-01T00:00:00+00:00", "cost_usd": 2.0, "kind": "compression"}
            ) + "\n")
            fp.write(json.dumps(
                {"ts": "2026-06-14T00:00:00+00:00", "cost_usd": 3.0, "kind": "compression"}
            ) + "\n")
        assert rolling_30d_spend(ledger_path, now=now) == pytest.approx(5.0)


class TestCheckCap:
    def test_under_cap_does_not_write_flag(
        self, ledger_path: Path, pause_flag: Path
    ) -> None:
        result = check_cap(
            ledger_path,
            cap_usd_monthly=10.0,
            pause_flag_path=pause_flag,
        )
        assert isinstance(result, CapCheck)
        assert result.over_cap is False
        assert result.pause_flag_written is False
        assert not pause_flag.exists()

    def test_over_cap_writes_flag(
        self, ledger_path: Path, pause_flag: Path
    ) -> None:
        now = datetime(2026, 6, 15, tzinfo=UTC)
        cur = now.strftime("%Y-%m")
        with ledger_path.open("w", encoding="utf-8") as fp:
            fp.write(json.dumps(
                {"ts": f"{cur}-01T00:00:00+00:00", "cost_usd": 15.0, "kind": "compression"}
            ) + "\n")
        result = check_cap(
            ledger_path,
            cap_usd_monthly=10.0,
            pause_flag_path=pause_flag,
            now=now,
        )
        assert result.over_cap is True
        assert result.pause_flag_written is True
        assert pause_flag.is_file()
        payload = json.loads(pause_flag.read_text(encoding="utf-8"))
        assert payload["reason"] == "cost_cap_exceeded"
        assert payload["spend_usd"] >= 10.0


class TestCorruptLedgerFailClosed:
    """Verify fail-closed semantics when the ledger file is corrupt.

    A corrupt ledger (file exists, no parseable records) must NEVER be
    silently treated as zero spend. Each entry point that reads spend must
    surface the corruption so the operator can intervene rather than letting
    LLM compression proceed unchecked past the cost cap.
    """

    def test_month_to_date_spend_raises_on_corrupt_ledger(
        self, ledger_path: Path
    ) -> None:
        ledger_path.write_text("not-json\nalso-not-json\n", encoding="utf-8")
        with pytest.raises(LedgerCorruptError, match="no parseable records"):
            month_to_date_spend(ledger_path)

    def test_rolling_30d_spend_raises_on_corrupt_ledger(
        self, ledger_path: Path
    ) -> None:
        ledger_path.write_text("<<<garbage>>>\n", encoding="utf-8")
        with pytest.raises(LedgerCorruptError):
            rolling_30d_spend(ledger_path)

    def test_check_cap_raises_on_corrupt_ledger(
        self, ledger_path: Path, pause_flag: Path
    ) -> None:
        ledger_path.write_text("bad-line\n", encoding="utf-8")
        with pytest.raises(LedgerCorruptError):
            check_cap(
                ledger_path,
                cap_usd_monthly=10.0,
                pause_flag_path=pause_flag,
            )

    def test_reserve_cost_raises_oserror_on_corrupt_ledger(
        self, ledger_path: Path
    ) -> None:
        """reserve_cost wraps LedgerCorruptError as OSError for the pipeline handler."""
        ledger_path.write_text("not-json\n", encoding="utf-8")
        with pytest.raises(OSError, match="ledger_corrupt"):
            reserve_cost(
                ledger_path,
                estimated_cost_usd=0.10,
                host="test-host",
                cap_usd_monthly=10.0,
            )

    def test_missing_ledger_still_yields_zero_spend(
        self, ledger_path: Path
    ) -> None:
        """A missing ledger file is not corrupt — 0.0 spend is correct."""
        assert not ledger_path.exists()
        assert month_to_date_spend(ledger_path) == 0.0
        assert rolling_30d_spend(ledger_path) == 0.0

    def test_missing_ledger_reserve_cost_proceeds(
        self, ledger_path: Path
    ) -> None:
        """A missing ledger allows reservation (0.0 spend, well below cap)."""
        assert not ledger_path.exists()
        _rid, ok = reserve_cost(
            ledger_path,
            estimated_cost_usd=0.10,
            host="test-host",
            cap_usd_monthly=10.0,
        )
        assert ok is True

    def test_partial_corruption_skips_bad_lines_gracefully(
        self, ledger_path: Path
    ) -> None:
        """A file with mixed good/bad lines uses only the parseable records."""
        now = datetime(2026, 6, 15, tzinfo=UTC)
        cur = now.strftime("%Y-%m")
        ledger_path.write_text(
            json.dumps({"ts": f"{cur}-01T00:00:00+00:00", "cost_usd": 3.0, "kind": "compression"})
            + "\nnot-json\n"
            + json.dumps({"ts": f"{cur}-02T00:00:00+00:00", "cost_usd": 2.0, "kind": "compression"})
            + "\n",
            encoding="utf-8",
        )
        # Should not raise — at least one good record exists.
        assert month_to_date_spend(ledger_path, now=now) == pytest.approx(5.0)
