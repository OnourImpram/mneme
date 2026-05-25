"""Concurrency tests for patterns and trajectory write paths (P1-1).

Both ``store_pattern`` and ``record_step`` perform a read-modify-write
cycle that was previously unprotected. The file_lock wrapper added in
P1-1 ensures no update is lost when two threads write the same file
concurrently.
"""

from __future__ import annotations

import threading
from pathlib import Path

import pytest

from mneme_core.patterns import Pattern, list_patterns, store_pattern
from mneme_core.trajectory import load_trajectory, record_step
from mneme_core.vault.config import VaultConfig


@pytest.fixture
def vault(tmp_path: Path) -> VaultConfig:
    v = VaultConfig.from_path(tmp_path)
    (v.root / ".mneme").mkdir(parents=True, exist_ok=True)
    return v


class TestPatternsConcurrency:
    def test_no_lost_update_under_parallel_writes(self, vault: VaultConfig) -> None:
        """Two threads writing distinct patterns must both land on disk."""
        names = [f"pattern-{i}" for i in range(10)]
        errors: list[Exception] = []

        def _write(name: str) -> None:
            try:
                store_pattern(
                    vault,
                    Pattern(name=name, signal=f"sig-{name}", action="act"),
                )
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=_write, args=(n,)) for n in names]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert errors == [], f"Thread errors: {errors}"
        stored = {p.name for p in list_patterns(vault)}
        assert stored == set(names), f"Missing patterns: {set(names) - stored}"

    def test_same_name_concurrent_writes_no_corruption(
        self, vault: VaultConfig
    ) -> None:
        """Multiple threads overwriting the same pattern name must not corrupt
        the file. The last write wins; the file must be parseable."""
        errors: list[Exception] = []

        def _write(i: int) -> None:
            try:
                store_pattern(
                    vault,
                    Pattern(
                        name="shared-pattern",
                        signal=f"signal-{i}",
                        action=f"action-{i}",
                    ),
                )
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=_write, args=(i,)) for i in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert errors == [], f"Thread errors: {errors}"
        patterns = list_patterns(vault)
        # Exactly one pattern with name "shared-pattern" must survive.
        shared = [p for p in patterns if p.name == "shared-pattern"]
        assert len(shared) == 1
        # The file must be fully parseable (no partial write corruption).
        assert shared[0].signal.startswith("signal-")


class TestTrajectoryConcurrency:
    def test_no_lost_steps_under_parallel_record_step(
        self, vault: VaultConfig
    ) -> None:
        """Ten threads each appending one step to the same trajectory must
        all land; no step may be lost."""
        n_threads = 10
        errors: list[Exception] = []
        barrier = threading.Barrier(n_threads)

        def _record(i: int) -> None:
            try:
                barrier.wait()  # maximize contention
                record_step(
                    vault,
                    "concurrent-session",
                    action=f"step-{i}",
                    observation=f"obs-{i}",
                )
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=_record, args=(i,)) for i in range(n_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert errors == [], f"Thread errors: {errors}"
        traj = load_trajectory(vault, "concurrent-session")
        assert traj is not None
        assert len(traj.steps) == n_threads, (
            f"Expected {n_threads} steps, got {len(traj.steps)}. "
            f"Missing: {set(range(n_threads)) - {int(s.action.split('-')[1]) for s in traj.steps}}"
        )
