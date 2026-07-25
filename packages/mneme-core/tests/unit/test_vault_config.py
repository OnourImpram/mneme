"""Unit tests for vault path resolution."""

from __future__ import annotations

import dataclasses
from pathlib import Path

import pytest

from mneme_core.vault.config import (
    DEFAULT_VAULT_NAME,
    MARKER_DIR,
    VaultConfig,
    VaultNotFoundError,
)


class TestResolutionOrder:
    def test_env_var_takes_priority(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        env_vault = tmp_path / "env-vault"
        env_vault.mkdir()
        monkeypatch.setenv("MNEME_VAULT", str(env_vault))
        cfg = VaultConfig.resolve(env={"MNEME_VAULT": str(env_vault)})
        assert cfg.root == env_vault.resolve()

    def test_explicit_used_when_no_env(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        path = tmp_path / "explicit"
        path.mkdir()
        cfg = VaultConfig.resolve(explicit=path, env={})
        assert cfg.root == path.resolve()

    def test_parent_marker_walk(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        vault = tmp_path / "myvault"
        vault.mkdir()
        (vault / MARKER_DIR).mkdir()
        deep = vault / "a" / "b"
        deep.mkdir(parents=True)
        cfg = VaultConfig.resolve(cwd=deep, home=tmp_path / "no-home", env={})
        assert cfg.root == vault.resolve()

    def test_default_home_vault_when_exists(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        default = tmp_path / DEFAULT_VAULT_NAME
        default.mkdir()
        cfg = VaultConfig.resolve(cwd=tmp_path, home=tmp_path, env={})
        assert cfg.root == default.resolve()

    def test_not_found_raises(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        empty_home = tmp_path / "no-home"
        empty_home.mkdir()
        cwd = empty_home / "elsewhere"
        cwd.mkdir()
        with pytest.raises(VaultNotFoundError):
            VaultConfig.resolve(cwd=cwd, home=empty_home, env={})

    def test_home_config_precedes_marker(self, tmp_path: Path) -> None:
        home = tmp_path / "home"
        config_dir = home / MARKER_DIR
        config_dir.mkdir(parents=True)
        configured_vault = tmp_path / "configured"
        configured_vault.mkdir()
        (config_dir / "config.toml").write_text(
            f'vault = "{configured_vault.as_posix()}"\n', encoding="utf-8"
        )
        marker_vault = tmp_path / "marker"
        (marker_vault / MARKER_DIR).mkdir(parents=True)

        cfg = VaultConfig.resolve(cwd=marker_vault, home=home, env={})

        assert cfg.root == configured_vault.resolve()


class TestDefaultScope:
    def test_environment_scope_takes_priority(self, tmp_path: Path) -> None:
        home = tmp_path / "home"
        config_dir = home / MARKER_DIR
        config_dir.mkdir(parents=True)
        (config_dir / "config.toml").write_text('default_scope = "configured"\n', encoding="utf-8")
        cfg = VaultConfig(root=tmp_path)

        scope = cfg.default_scope(env={"MNEME_SCOPE": "clinical"}, home=home)

        assert scope == "clinical"

    def test_home_config_supplies_default_scope(self, tmp_path: Path) -> None:
        home = tmp_path / "home"
        config_dir = home / MARKER_DIR
        config_dir.mkdir(parents=True)
        (config_dir / "config.toml").write_text('default_scope = "research"\n', encoding="utf-8")
        cfg = VaultConfig(root=tmp_path)

        scope = cfg.default_scope(env={}, home=home)

        assert scope == "research"

    def test_literal_default_when_scope_is_not_configured(self, tmp_path: Path) -> None:
        home = tmp_path / "home"
        home.mkdir()
        cfg = VaultConfig(root=tmp_path)

        scope = cfg.default_scope(env={}, home=home)

        assert scope == "default"

    def test_invalid_or_wildcard_environment_scope_falls_closed(self, tmp_path: Path) -> None:
        cfg = VaultConfig(root=tmp_path)
        assert cfg.default_scope(env={"MNEME_SCOPE": "*"}, home=tmp_path) == "default"
        assert cfg.default_scope(env={"MNEME_SCOPE": " clinical"}, home=tmp_path) == "default"


class TestDerivedPaths:
    def test_state_dir(self, tmp_path: Path) -> None:
        cfg = VaultConfig(root=tmp_path)
        assert cfg.state_dir == tmp_path / MARKER_DIR

    def test_fts5_db(self, tmp_path: Path) -> None:
        cfg = VaultConfig(root=tmp_path)
        assert cfg.fts5_db == tmp_path / MARKER_DIR / "fts5.sqlite"

    def test_telemetry_dir(self, tmp_path: Path) -> None:
        cfg = VaultConfig(root=tmp_path)
        assert cfg.telemetry_dir == tmp_path / MARKER_DIR / "telemetry"

    def test_staging_dir(self, tmp_path: Path) -> None:
        cfg = VaultConfig(root=tmp_path)
        assert cfg.staging_dir == tmp_path / MARKER_DIR / "staging"

    def test_audit_log_dir(self, tmp_path: Path) -> None:
        cfg = VaultConfig(root=tmp_path)
        assert cfg.audit_log_dir == tmp_path / MARKER_DIR / "audit"


class TestImmutability:
    def test_frozen_dataclass(self, tmp_path: Path) -> None:
        cfg = VaultConfig(root=tmp_path)
        with pytest.raises(dataclasses.FrozenInstanceError):
            cfg.root = tmp_path / "other"  # type: ignore[misc]
