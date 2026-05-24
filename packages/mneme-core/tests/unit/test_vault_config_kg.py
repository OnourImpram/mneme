"""Tests for the Phase E KG-derived paths on ``VaultConfig``."""

from __future__ import annotations

from pathlib import Path

from mneme_core.vault.config import VaultConfig


class TestKgDerivedPaths:
    def test_all_kg_paths_live_under_state_dir(self, tmp_path: Path) -> None:
        vault = VaultConfig.from_path(tmp_path)
        state = vault.state_dir
        assert vault.kg_queue_dir.is_relative_to(state)
        assert vault.kg_processed_dir.is_relative_to(state)
        assert vault.kg_active_flag.is_relative_to(state)
        assert vault.kg_credentials_path.is_relative_to(state)
        assert vault.kg_community_refresh_flag.is_relative_to(state)

    def test_worker_log_lives_under_telemetry(self, tmp_path: Path) -> None:
        vault = VaultConfig.from_path(tmp_path)
        assert vault.kg_worker_log.is_relative_to(vault.telemetry_dir)

    def test_cost_ledger_at_state_root(self, tmp_path: Path) -> None:
        vault = VaultConfig.from_path(tmp_path)
        assert vault.kg_cost_ledger.parent == vault.state_dir
        assert vault.kg_cost_ledger.name == "cost-ledger.jsonl"

    def test_paths_are_stable_across_calls(self, tmp_path: Path) -> None:
        vault = VaultConfig.from_path(tmp_path)
        assert vault.kg_queue_dir == vault.kg_queue_dir
        assert vault.kg_active_flag == vault.kg_active_flag

    def test_paths_unique(self, tmp_path: Path) -> None:
        vault = VaultConfig.from_path(tmp_path)
        all_paths = {
            vault.kg_queue_dir,
            vault.kg_processed_dir,
            vault.kg_active_flag,
            vault.kg_credentials_path,
            vault.kg_community_refresh_flag,
            vault.kg_worker_log,
            vault.kg_cost_ledger,
        }
        assert len(all_paths) == 7
