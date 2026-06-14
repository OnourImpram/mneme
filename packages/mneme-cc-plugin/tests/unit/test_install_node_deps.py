"""Regression: the npm executable is resolved before it is invoked.

On Windows ``npm`` is ``npm.cmd``/``npm.CMD``. A bare ``["npm", ...]`` argv
handed to ``subprocess.run`` (no shell) raises ``FileNotFoundError``
(WinError 2), so ``mneme install`` aborted before it could wire Claude Code.
The installer must resolve the executable with ``shutil.which`` (the same
mechanism the doctor check already uses) so the node step works on every
platform. Surfaced by the 3.1.0 self-hosted dogfood on a Windows host.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

import pytest

from mneme_cc_plugin.install import cli as install_cli
from mneme_cc_plugin.install.cli import CommandResult, Installer, InstallerConfig


def _config(tmp_path: Path) -> InstallerConfig:
    return InstallerConfig(
        profile="lite",
        vault_root=tmp_path / "vault",
        settings_path=tmp_path / "settings.json",
        backup_dir=tmp_path / "backups",
    )


def _ok_runner(captured: list[tuple[str, ...]]) -> install_cli.CommandRunner:
    def runner(args: Sequence[str]) -> CommandResult:
        captured.append(tuple(args))
        return CommandResult(args=tuple(args), returncode=0, stdout="", stderr="")

    return runner


def test_resolves_npm_executable_before_invoking(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    resolved = r"C:\fake\nodejs\npm.CMD"
    monkeypatch.setattr(
        install_cli.shutil,
        "which",
        lambda name: resolved if name == "npm" else None,
    )
    captured: list[tuple[str, ...]] = []
    inst = Installer(config=_config(tmp_path), runner=_ok_runner(captured))

    inst.install_node_deps()

    assert captured, "runner was never called"
    assert captured[0][0] == resolved, (
        f"npm argv[0] must be the resolved path, not a bare name; got {captured[0][0]!r}"
    )
    assert list(captured[0][1:]) == ["install", "-g", "mneme-mcp-server"]


def test_falls_back_to_bare_name_when_which_returns_none(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(install_cli.shutil, "which", lambda name: None)
    captured: list[tuple[str, ...]] = []
    inst = Installer(config=_config(tmp_path), runner=_ok_runner(captured))

    inst.install_node_deps()

    assert captured[0][0] == "npm"
