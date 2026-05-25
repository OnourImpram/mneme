"""Performance and behavioural tests for the counter-gated staging size cap.

Two required cases:
(a) Cap still enforced (behavioural parity): write past the cap, ensure a
    reconciliation occurs and total size is back under the cap.
(b) Hot path no longer scans every event: monkeypatch rglob to count calls,
    capture M sub-threshold events, assert rglob was called on the order of
    M/N times — definitively fewer than M.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from mneme_core.compression.staging import (
    StagingConfig,
    _SIZE_COUNTER_NAME,
    capture_event,
    enforce_size_cap,
)


@pytest.fixture
def config(tmp_path: Path) -> StagingConfig:
    return StagingConfig(
        staging_dir=tmp_path / "staging",
        audit_dir=tmp_path / "audit",
        host="testhost",
    )


def _event(data: str = "x") -> dict[str, Any]:
    return {"tool_name": "Edit", "data": data}


class TestSizeCapEnforced:
    """(a) Behavioural parity: cap is still honoured after counter-gated path."""

    def test_archives_oldest_when_cap_exceeded(self, tmp_path: Path) -> None:
        # Use a tight cap and reconcile_every_n_events=1 so every event
        # triggers a reconciliation — same behaviour as the old unconditional
        # enforce_size_cap call, making the parity assertion exact.
        config = StagingConfig(
            staging_dir=tmp_path / "staging",
            audit_dir=tmp_path / "audit",
            host="testhost",
            size_cap_bytes=300,
            reconcile_every_n_events=1,
        )

        # Write events with distinct mtimes so archive order is deterministic.
        capture_event(_event("a" * 200), config)
        time.sleep(0.05)
        capture_event(_event("b" * 200), config)

        # Archive directory must exist and contain at least one file.
        archive_dir = config.staging_dir / "archive"
        assert archive_dir.exists(), "archive directory should be created"
        archived = list(archive_dir.rglob("*.jsonl"))
        assert len(archived) >= 1, "at least one file should have been archived"

        # Total non-archive size must be under the cap.
        remaining = [
            f
            for f in config.staging_dir.rglob("*.jsonl")
            if "archive" not in f.parts
        ]
        total = sum(f.stat().st_size for f in remaining)
        assert total < config.size_cap_bytes, (
            f"remaining size {total} exceeds cap {config.size_cap_bytes}"
        )

    def test_size_under_cap_no_archive(self, config: StagingConfig) -> None:
        # With default 100 MB cap a tiny event must never trigger archival.
        config.reconcile_every_n_events = 1
        ok = capture_event(_event("small"), config)
        assert ok is True
        archive_dir = config.staging_dir / "archive"
        assert not archive_dir.exists() or not list(archive_dir.rglob("*.jsonl"))

    def test_counter_sidecar_is_not_jsonl(self, config: StagingConfig) -> None:
        # The sidecar must use a .json extension so rglob("*.jsonl") never
        # picks it up and erroneously counts it toward the size cap.
        config.reconcile_every_n_events = 1
        capture_event(_event("y"), config)
        sidecar = config.staging_dir / _SIZE_COUNTER_NAME
        assert sidecar.exists(), "size counter sidecar should be written"
        assert sidecar.suffix == ".json"
        # Confirm rglob does not match it.
        jsonl_files = list(config.staging_dir.rglob("*.jsonl"))
        assert sidecar not in jsonl_files

    def test_corrupt_sidecar_triggers_reconcile(self, tmp_path: Path) -> None:
        # If the sidecar is corrupt, _read_counter returns sentinel values that
        # force a reconciliation on the very next event — no exception raised.
        config = StagingConfig(
            staging_dir=tmp_path / "staging",
            audit_dir=tmp_path / "audit",
            host="testhost",
            size_cap_bytes=300,
            reconcile_every_n_events=100,
        )
        # Prime the staging dir.
        capture_event(_event("init"), config)
        # Corrupt the sidecar.
        sidecar = config.staging_dir / _SIZE_COUNTER_NAME
        sidecar.write_text("NOT JSON AT ALL !!!", encoding="utf-8")
        # Next capture must not raise and must rewrite a valid sidecar.
        ok = capture_event(_event("after-corrupt"), config)
        assert ok is True
        data = json.loads(sidecar.read_text(encoding="utf-8"))
        assert "running_bytes" in data
        assert "events_since_reconcile" in data


class TestHotPathRglobCount:
    """(b) rglob call count is O(M/N), not O(M), over M sub-threshold events."""

    def test_rglob_called_far_fewer_than_m_times(self, tmp_path: Path) -> None:
        N = 10   # reconcile_every_n_events — small so test is fast
        M = 55   # events to capture  (5.5 × N → expect ~5-6 reconciliations)

        config = StagingConfig(
            staging_dir=tmp_path / "staging",
            audit_dir=tmp_path / "audit",
            host="testhost",
            size_cap_bytes=100 * 1024 * 1024,  # 100 MB — cap never triggered
            reconcile_every_n_events=N,
        )

        rglob_calls: list[str] = []
        original_rglob = Path.rglob

        def counting_rglob(self: Path, pattern: str) -> Any:  # type: ignore[override]
            rglob_calls.append(pattern)
            return original_rglob(self, pattern)

        with patch.object(Path, "rglob", counting_rglob):
            for i in range(M):
                ok = capture_event(_event(f"payload-{i}"), config)
                assert ok is True, f"capture_event returned False at i={i}"

        # Each reconciliation fires TWO rglob calls: one inside enforce_size_cap
        # and one inside _measure_staging_bytes.  Expected reconciliations is
        # roughly M // N (plus 1 for the first-event missing-sidecar sentinel).
        # Allow +2 boundary events → budget = (M // N + 3) * 2.
        expected_reconciles_max = (M // N) + 3
        expected_rglob_max = expected_reconciles_max * 2

        # Primary assertion: rglob count must be well below M (old O(M) path).
        assert len(rglob_calls) < M, (
            f"rglob called {len(rglob_calls)} times for {M} events — "
            "still scanning on every event"
        )
        assert len(rglob_calls) <= expected_rglob_max, (
            f"rglob called {len(rglob_calls)} times; expected at most "
            f"{expected_rglob_max} (≈ (M/N + 3) * 2 = ({M}//{N} + 3) * 2)"
        )

    def test_rglob_fires_on_nth_event_boundary(self, tmp_path: Path) -> None:
        # Confirm the periodic reconciliation fires at the N-th boundary and
        # not on every intermediate event.
        #
        # Note: event 0 always fires a reconciliation because the sidecar is
        # missing and _read_counter returns the sentinel (0, N) which satisfies
        # events_since_reconcile >= N immediately.  After that first reconcile
        # the counter resets, so events 1..(N-1) must be rglob-free, and event
        # N fires the next periodic reconcile.
        N = 5
        config = StagingConfig(
            staging_dir=tmp_path / "staging",
            audit_dir=tmp_path / "audit",
            host="testhost",
            size_cap_bytes=100 * 1024 * 1024,
            reconcile_every_n_events=N,
        )

        rglob_calls: list[int] = []  # store event index when rglob fires
        original_rglob = Path.rglob
        captured_index: list[int] = [0]

        def counting_rglob(self: Path, pattern: str) -> Any:  # type: ignore[override]
            rglob_calls.append(captured_index[0])
            return original_rglob(self, pattern)

        with patch.object(Path, "rglob", counting_rglob):
            for i in range(N * 2):
                captured_index[0] = i
                capture_event(_event(f"ev-{i}"), config)

        # Events 1..(N-1) must be rglob-free (the "quiet zone" after the
        # first-event sentinel reconcile and before the next N-boundary).
        quiet_zone = [idx for idx in rglob_calls if 1 <= idx < N]
        assert quiet_zone == [], (
            f"rglob fired in quiet zone (events 1..{N-1}): indices {quiet_zone}"
        )

        # Total rglob calls must be well below N*2 (would be N*2*2 if every
        # event scanned — 2 rglobs per reconcile × N*2 events).
        assert len(rglob_calls) < N * 2, (
            f"rglob called {len(rglob_calls)} times across {N * 2} events — "
            "too frequent"
        )
