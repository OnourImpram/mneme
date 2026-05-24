"""Tests for the Phase F compression-derived paths on VaultConfig."""

from __future__ import annotations

from pathlib import Path

from mneme_core.vault.config import VaultConfig


class TestCompressionPaths:
    def test_config_path_under_state_dir(self, tmp_path: Path) -> None:
        vault = VaultConfig.from_path(tmp_path)
        assert vault.compression_config_path.parent == vault.state_dir
        assert vault.compression_config_path.name == "compression.json"

    def test_pause_flag_under_state_dir(self, tmp_path: Path) -> None:
        vault = VaultConfig.from_path(tmp_path)
        assert vault.compression_pause_flag.parent == vault.state_dir
        assert vault.compression_pause_flag.name == "compression-paused.flag"

    def test_daily_log_dir_under_vault_root(self, tmp_path: Path) -> None:
        vault = VaultConfig.from_path(tmp_path)
        assert vault.compression_daily_log_dir.parent == vault.root
        assert vault.compression_daily_log_dir.name == "sessions"

    def test_paths_unique(self, tmp_path: Path) -> None:
        vault = VaultConfig.from_path(tmp_path)
        assert vault.compression_config_path != vault.compression_pause_flag
        assert vault.compression_config_path != vault.compression_daily_log_dir
        assert vault.compression_pause_flag != vault.compression_daily_log_dir

    def test_paths_stable(self, tmp_path: Path) -> None:
        vault = VaultConfig.from_path(tmp_path)
        assert vault.compression_config_path == vault.compression_config_path
        assert vault.compression_pause_flag == vault.compression_pause_flag
