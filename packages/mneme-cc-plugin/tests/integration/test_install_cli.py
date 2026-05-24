"""Integration tests for the mneme install CLI.

Click's ``CliRunner`` exercises the dispatcher without spawning real
subprocesses. The ``Installer`` class is also tested directly with
an injected runner that records calls.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from mneme_cc_plugin.install.cli import (
    CommandResult,
    Installer,
    InstallerConfig,
    cli,
)
from mneme_cc_plugin.install.settings import read_settings


def _initial_settings_json(path: Path, contents: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(contents, indent=2), encoding="utf-8")


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


@pytest.fixture
def workspace(tmp_path: Path) -> dict[str, Path]:
    settings = tmp_path / ".claude" / "settings.json"
    _initial_settings_json(settings, {"existing": True})
    return {
        "vault": tmp_path / "vault",
        "settings": settings,
        "backup": tmp_path / "backups",
    }


class TestCliDispatcher:
    def test_help_lists_subcommands(self, runner: CliRunner) -> None:
        res = runner.invoke(cli, ["--help"])
        assert res.exit_code == 0
        for sub in ("install", "upgrade", "uninstall", "doctor"):
            assert sub in res.output

    def test_version_flag(self, runner: CliRunner) -> None:
        res = runner.invoke(cli, ["--version"])
        assert res.exit_code == 0
        assert "mneme" in res.output

    def test_doctor_prints_json(
        self, runner: CliRunner, workspace: dict[str, Path]
    ) -> None:
        res = runner.invoke(
            cli,
            [
                "doctor",
                "--vault", str(workspace["vault"]),
                "--settings", str(workspace["settings"]),
                "--profile", "lite",
            ],
        )
        assert res.exit_code == 0
        report = json.loads(res.output)
        assert report["profile"] == "lite"
        assert report["settings_exists"] is True

    def test_install_dry_run_does_not_mutate(
        self, runner: CliRunner, workspace: dict[str, Path]
    ) -> None:
        res = runner.invoke(
            cli,
            [
                "install",
                "--vault", str(workspace["vault"]),
                "--settings", str(workspace["settings"]),
                "--backup-dir", str(workspace["backup"]),
                "--profile", "lite",
                "--skip-python",
                "--skip-node",
                "--dry-run",
            ],
        )
        assert res.exit_code == 0, res.output
        # Backup dir should not be created since no real write happened.
        assert not workspace["backup"].exists()
        # Vault marker IS created (it is local-only filesystem prep, not
        # destructive).
        assert (workspace["vault"] / ".mneme").exists()

    def test_install_writes_hooks_and_mcp(
        self, runner: CliRunner, workspace: dict[str, Path]
    ) -> None:
        res = runner.invoke(
            cli,
            [
                "install",
                "--vault", str(workspace["vault"]),
                "--settings", str(workspace["settings"]),
                "--backup-dir", str(workspace["backup"]),
                "--profile", "lite",
                "--skip-python",
                "--skip-node",
            ],
        )
        assert res.exit_code == 0, res.output
        data = read_settings(workspace["settings"])
        assert "PostToolUse" in data["hooks"]  # type: ignore[index]
        assert data["mcpServers"]["mneme"]["command"] == "mneme-mcp"  # type: ignore[index]
        # Foreign keys preserved.
        assert data["existing"] is True  # type: ignore[index]
        # Backup exists.
        assert any(workspace["backup"].iterdir())

    def test_uninstall_removes_only_mneme_entries(
        self, runner: CliRunner, workspace: dict[str, Path]
    ) -> None:
        install = runner.invoke(
            cli,
            [
                "install",
                "--vault", str(workspace["vault"]),
                "--settings", str(workspace["settings"]),
                "--backup-dir", str(workspace["backup"]),
                "--profile", "lite",
                "--skip-python",
                "--skip-node",
            ],
        )
        assert install.exit_code == 0, install.output

        # Add a foreign hook entry by hand.
        data = read_settings(workspace["settings"])
        data["hooks"]["Stop"].append(  # type: ignore[index]
            {"hooks": [{"type": "command", "command": "user-hook"}]}
        )
        workspace["settings"].write_text(
            json.dumps(data, indent=2), encoding="utf-8"
        )

        out = runner.invoke(
            cli,
            [
                "uninstall",
                "--settings", str(workspace["settings"]),
                "--backup-dir", str(workspace["backup"]),
            ],
        )
        assert out.exit_code == 0, out.output
        post = read_settings(workspace["settings"])
        # mneme MCP entry removed.
        assert "mneme" not in post.get("mcpServers", {})  # type: ignore[union-attr]
        # User-supplied hook still present.
        stop_handlers = post["hooks"]["Stop"]  # type: ignore[index]
        flattened = [
            e
            for h in stop_handlers
            for e in h.get("hooks", [])
        ]
        assert any(e["command"] == "user-hook" for e in flattened)
        # No mneme-tagged hooks remain.
        assert not any(e.get("_mneme_tag") == "mneme" for e in flattened)


class TestInstallerUnit:
    def test_detect_interpreter_windows(self, monkeypatch: pytest.MonkeyPatch) -> None:
        cfg = InstallerConfig(
            profile="lite",
            vault_root=Path("v"),
            settings_path=Path("s"),
            backup_dir=Path("b"),
        )
        inst = Installer(config=cfg)
        monkeypatch.setattr("sys.platform", "win32")
        assert inst.detect_interpreter() == ["py", "-3"]

    def test_detect_interpreter_posix(self, monkeypatch: pytest.MonkeyPatch) -> None:
        cfg = InstallerConfig(
            profile="lite",
            vault_root=Path("v"),
            settings_path=Path("s"),
            backup_dir=Path("b"),
        )
        inst = Installer(config=cfg)
        monkeypatch.setattr("sys.platform", "linux")
        assert inst.detect_interpreter() == ["python3"]

    def test_runner_failure_raises_click_exception(
        self, workspace: dict[str, Path]
    ) -> None:
        calls: list[tuple[str, ...]] = []

        def failing_runner(args):  # type: ignore[no-untyped-def]
            calls.append(tuple(args))
            return CommandResult(args=tuple(args), returncode=1, stdout="", stderr="boom")

        cfg = InstallerConfig(
            profile="standard",
            vault_root=workspace["vault"],
            settings_path=workspace["settings"],
            backup_dir=workspace["backup"],
        )
        inst = Installer(config=cfg, runner=failing_runner)
        import click as _click

        with pytest.raises(_click.ClickException):
            inst.install_python_deps()
        assert calls and calls[0][0:3] == ("python", "-m", "pip") or calls[0][1:3] == ("-m", "pip")

    def test_register_hooks_idempotent(
        self, workspace: dict[str, Path]
    ) -> None:
        cfg = InstallerConfig(
            profile="lite",
            vault_root=workspace["vault"],
            settings_path=workspace["settings"],
            backup_dir=workspace["backup"],
        )
        inst = Installer(config=cfg)
        inst.register_hooks()
        first = read_settings(workspace["settings"])

        inst.register_hooks()
        second = read_settings(workspace["settings"])
        # Same hook count after running twice.
        for event in first["hooks"]:  # type: ignore[union-attr]
            assert len(first["hooks"][event]) == len(second["hooks"][event])  # type: ignore[index]
