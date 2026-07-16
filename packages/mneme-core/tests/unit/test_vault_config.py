"""Unit tests for vault path resolution."""

from __future__ import annotations

import dataclasses
import sys
from pathlib import Path

import pytest

from mneme_core.vault.config import (
    DEFAULT_VAULT_NAME,
    MARKER_DIR,
    VaultConfig,
    VaultNotFoundError,
)


def _set_home(monkeypatch: pytest.MonkeyPatch, path: Path) -> None:
    monkeypatch.setenv("HOME", str(path))
    if sys.platform == "win32":
        monkeypatch.setenv("USERPROFILE", str(path))


class TestResolutionOrder:
    def test_env_var_takes_priority(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        env_vault = tmp_path / "env-vault"
        env_vault.mkdir()
        monkeypatch.setenv("MNEME_VAULT", str(env_vault))
        cfg = VaultConfig.resolve()
        assert cfg.root == env_vault.resolve()

    def test_explicit_used_when_no_env(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.delenv("MNEME_VAULT", raising=False)
        path = tmp_path / "explicit"
        path.mkdir()
        cfg = VaultConfig.resolve(explicit=path)
        assert cfg.root == path.resolve()

    def test_parent_marker_walk(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.delenv("MNEME_VAULT", raising=False)
        _set_home(monkeypatch, tmp_path / "no-home")
        vault = tmp_path / "myvault"
        vault.mkdir()
        (vault / MARKER_DIR).mkdir()
        deep = vault / "a" / "b"
        deep.mkdir(parents=True)
        monkeypatch.chdir(deep)
        cfg = VaultConfig.resolve()
        assert cfg.root == vault.resolve()

    def test_default_home_vault_when_exists(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.delenv("MNEME_VAULT", raising=False)
        _set_home(monkeypatch, tmp_path)
        default = tmp_path / DEFAULT_VAULT_NAME
        default.mkdir()
        monkeypatch.chdir(tmp_path)
        cfg = VaultConfig.resolve()
        assert cfg.root == default.resolve()

    def test_not_found_raises(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.delenv("MNEME_VAULT", raising=False)
        empty_home = tmp_path / "no-home"
        empty_home.mkdir()
        _set_home(monkeypatch, empty_home)
        cwd = tmp_path / "elsewhere"
        cwd.mkdir()
        monkeypatch.chdir(cwd)
        with pytest.raises(VaultNotFoundError):
            VaultConfig.resolve()


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


class TestDefaultScope:
    def test_env_scope_takes_priority(self, tmp_path: Path) -> None:
        cfg = VaultConfig(root=tmp_path)
        assert cfg.default_scope({"MNEME_SCOPE": "clinical"}) == "clinical"

    def test_wildcard_cannot_become_implicit_default(self, tmp_path: Path) -> None:
        cfg = VaultConfig(root=tmp_path)
        assert cfg.default_scope({"MNEME_SCOPE": "*"}) == "default"

    def test_invalid_scope_falls_back_to_default(self, tmp_path: Path) -> None:
        cfg = VaultConfig(root=tmp_path)
        assert cfg.default_scope({"MNEME_SCOPE": " clinical "}) == "default"
        assert cfg.default_scope({"MNEME_SCOPE": "case*all"}) == "default"

    def test_toml_default_scope(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        _set_home(monkeypatch, tmp_path)
        config_dir = tmp_path / MARKER_DIR
        config_dir.mkdir()
        (config_dir / "config.toml").write_text(
            'default_scope = "research"\n', encoding="utf-8"
        )
        cfg = VaultConfig(root=tmp_path / "vault")
        assert cfg.default_scope({}) == "research"

    def test_invalid_toml_scope_falls_back(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        _set_home(monkeypatch, tmp_path)
        config_dir = tmp_path / MARKER_DIR
        config_dir.mkdir()
        (config_dir / "config.toml").write_text(
            'default_scope = "*"\n', encoding="utf-8"
        )
        cfg = VaultConfig(root=tmp_path / "vault")
        assert cfg.default_scope({}) == "default"
