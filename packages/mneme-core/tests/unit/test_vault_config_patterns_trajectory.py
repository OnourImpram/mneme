"""Tests for Phase F.6 patterns and trajectory paths on VaultConfig."""

from __future__ import annotations

from pathlib import Path

from mneme_core.vault.config import VaultConfig


class TestNewPaths:
    def test_patterns_dir_under_root(self, tmp_path: Path) -> None:
        vault = VaultConfig.from_path(tmp_path)
        assert vault.patterns_dir.parent == vault.root
        assert vault.patterns_dir.name == "patterns"

    def test_trajectories_dir_under_root(self, tmp_path: Path) -> None:
        vault = VaultConfig.from_path(tmp_path)
        assert vault.trajectories_dir.parent == vault.root
        assert vault.trajectories_dir.name == "trajectories"

    def test_paths_unique(self, tmp_path: Path) -> None:
        vault = VaultConfig.from_path(tmp_path)
        assert vault.patterns_dir != vault.trajectories_dir
        assert vault.patterns_dir != vault.compression_daily_log_dir

    def test_paths_stable(self, tmp_path: Path) -> None:
        vault = VaultConfig.from_path(tmp_path)
        assert vault.patterns_dir == vault.patterns_dir
        assert vault.trajectories_dir == vault.trajectories_dir
