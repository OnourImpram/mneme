"""Integration tests for the session trajectory recorder."""

from __future__ import annotations

from pathlib import Path

import pytest

from mneme_core.trajectory import (
    end_trajectory,
    list_trajectories,
    load_trajectory,
    record_step,
    start_trajectory,
)
from mneme_core.vault.config import VaultConfig


@pytest.fixture
def vault(tmp_path: Path) -> VaultConfig:
    v = VaultConfig.from_path(tmp_path)
    (v.root / ".mneme").mkdir(parents=True, exist_ok=True)
    return v


class TestStartTrajectory:
    def test_creates_file_with_frontmatter(self, vault: VaultConfig) -> None:
        path = start_trajectory(vault, "s-1")
        assert path is not None
        assert path.is_file()
        text = path.read_text(encoding="utf-8")
        assert "type: trajectory" in text
        assert "session_id: s-1" in text

    def test_idempotent(self, vault: VaultConfig) -> None:
        first = start_trajectory(vault, "s-1")
        second = start_trajectory(vault, "s-1")
        assert first == second
        files = list(vault.trajectories_dir.rglob("*.md"))
        assert len(files) == 1

    def test_unsafe_session_id_sanitized(self, vault: VaultConfig) -> None:
        path = start_trajectory(vault, "../../etc/passwd")
        assert path is not None
        assert ".." not in path.name


class TestRecordStep:
    def test_appends_step_to_file(self, vault: VaultConfig) -> None:
        record_step(
            vault,
            "s-step",
            action="search",
            input_summary="query=rrf",
            observation="3 hits returned",
        )
        traj = load_trajectory(vault, "s-step")
        assert traj is not None
        assert len(traj.steps) == 1
        assert traj.steps[0].action == "search"
        assert "query=rrf" in traj.steps[0].input_summary
        assert "3 hits" in traj.steps[0].observation

    def test_multiple_steps_recorded_in_order(self, vault: VaultConfig) -> None:
        for i in range(3):
            record_step(
                vault,
                "s-multi",
                action=f"step_{i}",
                observation=f"obs_{i}",
            )
        traj = load_trajectory(vault, "s-multi")
        assert traj is not None
        actions = [s.action for s in traj.steps]
        assert actions == ["step_0", "step_1", "step_2"]


class TestEndTrajectory:
    def test_stamps_ended_at(self, vault: VaultConfig) -> None:
        record_step(vault, "s-end", action="ping")
        assert end_trajectory(vault, "s-end") is True
        traj = load_trajectory(vault, "s-end")
        assert traj is not None
        assert traj.ended_at is not None

    def test_returns_false_for_unknown(self, vault: VaultConfig) -> None:
        assert end_trajectory(vault, "never-started") is False


class TestLoadAndList:
    def test_load_missing_returns_none(self, vault: VaultConfig) -> None:
        assert load_trajectory(vault, "nope") is None

    def test_list_empty_returns_empty(self, vault: VaultConfig) -> None:
        assert list_trajectories(vault) == []

    def test_list_collects_today(self, vault: VaultConfig) -> None:
        for sid in ["a", "b", "c"]:
            record_step(vault, sid, action="x")
        trajs = list_trajectories(vault)
        ids = sorted(t.session_id for t in trajs)
        assert ids == ["a", "b", "c"]

    def test_list_respects_date_window(self, vault: VaultConfig) -> None:
        # Create files in different date dirs manually to test the window.
        old_dir = vault.trajectories_dir / "2020-01-01"
        old_dir.mkdir(parents=True)
        (old_dir / "old.md").write_text(
            "---\ntype: trajectory\nsession_id: old\n"
            "started_at: 2020-01-01T00:00:00+00:00\nended_at: null\n"
            "schema_version: 1.0.0\n---\n\n# Trajectory\n",
            encoding="utf-8",
        )
        record_step(vault, "today", action="x")
        # date_from in the future drops both.
        assert list_trajectories(vault, date_from="2099-01-01") == []
        # date_to in the past drops today only.
        only_old = list_trajectories(vault, date_to="2020-12-31")
        assert [t.session_id for t in only_old] == ["old"]
