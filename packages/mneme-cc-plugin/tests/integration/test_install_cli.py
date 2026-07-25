"""Integration tests for the mneme install CLI.

Click's ``CliRunner`` exercises the dispatcher without spawning real
subprocesses. The ``Installer`` class is also tested directly with
an injected runner that records calls.
"""

from __future__ import annotations

import json
import sys
import tomllib
from collections.abc import Callable
from pathlib import Path

import mneme_core.cli as core_cli
import pytest
from click.testing import CliRunner

from mneme_cc_plugin.install.cli import (
    CODEX_BLOCK_START,
    CodexTarget,
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
        for sub in (
            "install",
            "upgrade",
            "uninstall",
            "doctor",
            "hook",
            "index",
            "kg",
            "compress",
            "patterns",
            "trajectory",
            "audit",
            "audit-log",
            "version",
        ):
            assert sub in res.output

    def test_version_flag(self, runner: CliRunner) -> None:
        res = runner.invoke(cli, ["--version"])
        assert res.exit_code == 0
        assert "mneme" in res.output

    def test_version_command(self, runner: CliRunner) -> None:
        res = runner.invoke(cli, ["version"])
        assert res.exit_code == 0
        assert res.output.strip()

    @pytest.mark.parametrize(
        ("args", "expected"),
        [
            (["index", "--help"], "rebuild"),
            (["compress", "--help"], "status"),
            (["patterns", "--help"], "list"),
            (["trajectory", "--help"], "list"),
            (["audit", "--help"], "token"),
            (["audit-log", "--help"], "redaction"),
        ],
    )
    def test_core_commands_are_exposed(
        self, runner: CliRunner, args: list[str], expected: str
    ) -> None:
        res = runner.invoke(cli, args)
        assert res.exit_code == 0, res.output
        assert expected in res.output

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

    def test_doctor_verify_isolation_delegates_to_disposable_fixture(
        self,
        runner: CliRunner,
        workspace: dict[str, Path],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(
            core_cli,
            "verify_isolation_boundaries",
            lambda: [
                {
                    "name": "isolation_scope",
                    "status": "ok",
                    "detail": "temporary fixture passed",
                }
            ],
        )

        res = runner.invoke(
            cli,
            [
                "doctor",
                "--vault",
                str(workspace["vault"]),
                "--settings",
                str(workspace["settings"]),
                "--verify-isolation",
            ],
        )

        assert res.exit_code == 0, res.output
        report = json.loads(res.output)
        assert report["isolation"]["overall"] == "ok"
        assert report["isolation"]["checks"][0]["name"] == "isolation_scope"

    def test_doctor_verify_isolation_fails_closed(
        self,
        runner: CliRunner,
        workspace: dict[str, Path],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(
            core_cli,
            "verify_isolation_boundaries",
            lambda: [
                {
                    "name": "isolation_redaction",
                    "status": "fail",
                    "detail": "temporary fixture failed",
                }
            ],
        )

        res = runner.invoke(
            cli,
            [
                "doctor",
                "--vault",
                str(workspace["vault"]),
                "--settings",
                str(workspace["settings"]),
                "--verify-isolation",
            ],
        )

        assert res.exit_code == 1
        report = json.loads(res.output)
        assert report["isolation"]["overall"] == "fail"

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
        assert not (workspace["vault"] / ".mneme").exists()
        assert "vault (dry-run): would create marker" in res.output

    def test_install_upgrade_profile_alias(
        self, runner: CliRunner, workspace: dict[str, Path]
    ) -> None:
        res = runner.invoke(
            cli,
            [
                "install",
                "--upgrade-profile", "standard",
                "--vault", str(workspace["vault"]),
                "--settings", str(workspace["settings"]),
                "--backup-dir", str(workspace["backup"]),
                "--skip-python",
                "--skip-node",
                "--dry-run",
            ],
        )
        assert res.exit_code == 0, res.output
        assert "profile=standard" in res.output

    def test_install_upgrade_profile_alias_persists_existing_vault_profile(
        self, runner: CliRunner, workspace: dict[str, Path]
    ) -> None:
        first = runner.invoke(
            cli,
            [
                "install",
                "--profile",
                "lite",
                "--vault",
                str(workspace["vault"]),
                "--settings",
                str(workspace["settings"]),
                "--backup-dir",
                str(workspace["backup"]),
                "--skip-python",
                "--skip-node",
            ],
        )
        assert first.exit_code == 0, first.output
        config_path = workspace["vault"] / ".mneme" / "config.toml"
        with config_path.open("a", encoding="utf-8") as handle:
            handle.write("# operator setting\n[retrieval]\nlocale = \"tr\"\n")

        second = runner.invoke(
            cli,
            [
                "install",
                "--upgrade-profile",
                "standard",
                "--vault",
                str(workspace["vault"]),
                "--settings",
                str(workspace["settings"]),
                "--backup-dir",
                str(workspace["backup"]),
                "--skip-python",
                "--skip-node",
            ],
        )

        assert second.exit_code == 0, second.output
        persisted = tomllib.loads(config_path.read_text(encoding="utf-8"))
        assert persisted["profile"] == "standard"
        assert persisted["retrieval"]["locale"] == "tr"
        assert "# operator setting" in config_path.read_text(encoding="utf-8")

    def test_upgrade_command_persists_profile(
        self, runner: CliRunner, workspace: dict[str, Path]
    ) -> None:
        marker = workspace["vault"] / ".mneme"
        marker.mkdir(parents=True)
        (marker / "config.toml").write_text(
            'profile = "lite"\nschema_version = 1\n',
            encoding="utf-8",
        )

        result = runner.invoke(
            cli,
            [
                "upgrade",
                "--profile",
                "full",
                "--vault",
                str(workspace["vault"]),
                "--skip-python",
            ],
        )

        assert result.exit_code == 0, result.output
        persisted = tomllib.loads(
            (marker / "config.toml").read_text(encoding="utf-8")
        )
        assert persisted["profile"] == "full"

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
        assert (calls and calls[0][0:3] == ("python", "-m", "pip")) or calls[0][
            1:3
        ] == ("-m", "pip")

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


_HOOK_EVENT_TO_MODULE = {
    "session-start": "session_start",
    "post-tool-use": "post_tool_use",
    "stop": "stop",
    "pre-compact": "pre_compact",
    "session-end": "session_end",
}


class TestHookDispatch:
    """`mneme hook <event>` routes each CLI event to its hook module.

    The native plugin hooks.json (Claude Code and Codex) call
    `mneme hook <event>`. These tests pin the dispatch table so a future
    edit cannot silently cross-wire one event to another module.
    """

    @pytest.mark.parametrize("event", list(_HOOK_EVENT_TO_MODULE))
    def test_routes_to_matching_module(
        self,
        runner: CliRunner,
        monkeypatch: pytest.MonkeyPatch,
        event: str,
    ) -> None:
        from mneme_cc_plugin.hooks import (
            post_tool_use,
            pre_compact,
            session_end,
            session_start,
            stop,
        )

        modules = {
            "session_start": session_start,
            "post_tool_use": post_tool_use,
            "stop": stop,
            "pre_compact": pre_compact,
            "session_end": session_end,
        }
        called: list[str] = []

        def make_fake(name: str) -> Callable[[], int]:
            def _fake() -> int:
                called.append(name)
                return 0

            return _fake

        # Patch every module's main so a misroute records the wrong name.
        for name, mod in modules.items():
            monkeypatch.setattr(mod, "main", make_fake(name))

        res = runner.invoke(cli, ["hook", event])
        assert res.exit_code == 0, res.output
        assert called == [_HOOK_EVENT_TO_MODULE[event]]

    def test_rejects_unknown_event(self, runner: CliRunner) -> None:
        res = runner.invoke(cli, ["hook", "bogus"])
        assert res.exit_code != 0
        assert "bogus" in res.output or "Invalid value" in res.output


class TestCodexTarget:
    """`mneme install --client=codex` wires the MCP server into config.toml.

    Codex hooks and skills ship via the Codex marketplace plugin; the
    installer only writes the MCP server table, bracketed by managed
    sentinels so uninstall leaves the user's own config untouched.
    """

    def test_install_writes_mcp_block(
        self, runner: CliRunner, tmp_path: Path
    ) -> None:
        codex_config = tmp_path / ".codex" / "config.toml"
        res = runner.invoke(
            cli,
            [
                "install",
                "--client", "codex",
                "--vault", str(tmp_path / "vault"),
                "--codex-config", str(codex_config),
                "--skip-python",
                "--skip-node",
            ],
        )
        assert res.exit_code == 0, res.output
        text = codex_config.read_text(encoding="utf-8")
        assert "[mcp_servers.mneme]" in text
        assert 'command = "mneme-mcp"' in text
        assert "MNEME_VAULT" in text

    def test_install_codex_is_idempotent(
        self, runner: CliRunner, tmp_path: Path
    ) -> None:
        codex_config = tmp_path / "config.toml"
        args = [
            "install",
            "--client", "codex",
            "--vault", str(tmp_path / "vault"),
            "--codex-config", str(codex_config),
            "--skip-python",
            "--skip-node",
        ]
        assert runner.invoke(cli, args).exit_code == 0
        assert runner.invoke(cli, args).exit_code == 0
        assert codex_config.read_text(encoding="utf-8").count("[mcp_servers.mneme]") == 1

    def test_uninstall_removes_block_preserving_user_config(
        self, runner: CliRunner, tmp_path: Path
    ) -> None:
        codex_config = tmp_path / "config.toml"
        codex_config.write_text(
            'model = "gpt-5"\n\n[mcp_servers.other]\ncommand = "other-mcp"\n',
            encoding="utf-8",
        )
        common = ["--client", "codex", "--codex-config", str(codex_config)]
        install = runner.invoke(
            cli,
            ["install", *common, "--vault", str(tmp_path / "vault"),
             "--skip-python", "--skip-node"],
        )
        assert install.exit_code == 0, install.output
        assert "[mcp_servers.mneme]" in codex_config.read_text(encoding="utf-8")

        out = runner.invoke(cli, ["uninstall", *common])
        assert out.exit_code == 0, out.output
        text = codex_config.read_text(encoding="utf-8")
        assert "[mcp_servers.mneme]" not in text
        assert "mneme (managed)" not in text
        # User-authored config survives untouched.
        assert 'model = "gpt-5"' in text
        assert "[mcp_servers.other]" in text

    def test_target_round_trip(self, tmp_path: Path) -> None:
        cfg = tmp_path / "config.toml"
        target = CodexTarget(config_path=cfg)
        assert "registered" in target.register(tmp_path / "vault")
        assert CODEX_BLOCK_START in cfg.read_text(encoding="utf-8")
        # Second register is a no-op.
        assert "already present" in target.register(tmp_path / "vault")
        target.unregister()
        assert CODEX_BLOCK_START not in cfg.read_text(encoding="utf-8")


class TestHookCommandEscaping:
    """F2: _hook_command must produce a well-formed command string.

    On Windows the interpreter is wrapped in double-quotes.  On POSIX
    shlex.quote is used, which correctly handles spaces and embedded
    single-quotes.
    """

    def _make_installer(self, tmp_path: Path) -> Installer:
        settings = tmp_path / "settings.json"
        settings.write_text("{}", encoding="utf-8")
        cfg = InstallerConfig(
            profile="lite",
            vault_root=tmp_path / "vault",
            settings_path=settings,
            backup_dir=tmp_path / "bak",
        )
        return Installer(config=cfg)

    def test_win32_path_with_space_is_double_quoted(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        inst = self._make_installer(tmp_path)
        monkeypatch.setattr("sys.platform", "win32")
        monkeypatch.setattr(
            "mneme_cc_plugin.install.cli.shutil.which", lambda _: None
        )
        monkeypatch.setattr(sys, "executable", r"C:\Program Files\Python\python.exe")
        cmd = inst._hook_command("Stop")
        assert cmd.startswith('"C:\\Program Files\\Python\\python.exe"')
        assert "mneme_cc_plugin.install.cli hook stop" in cmd

    def test_posix_path_with_space_is_shell_quoted(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        inst = self._make_installer(tmp_path)
        monkeypatch.setattr("sys.platform", "linux")
        monkeypatch.setattr(
            "mneme_cc_plugin.install.cli.shutil.which", lambda _: None
        )
        monkeypatch.setattr(sys, "executable", "/home/alice/my venv/bin/python")
        cmd = inst._hook_command("Stop")
        # shlex.quote wraps with single-quotes for paths containing spaces.
        assert "'/home/alice/my venv/bin/python'" in cmd
        assert "mneme_cc_plugin.install.cli hook stop" in cmd

    def test_posix_path_with_single_quote_is_escaped(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        inst = self._make_installer(tmp_path)
        monkeypatch.setattr("sys.platform", "linux")
        monkeypatch.setattr(
            "mneme_cc_plugin.install.cli.shutil.which", lambda _: None
        )
        monkeypatch.setattr(sys, "executable", "/home/alice/it's/python")
        cmd = inst._hook_command("Stop")
        import shlex as _shlex

        # Round-trip: splitting the produced command must not raise and
        # the first token must resolve back to the original executable.
        tokens = _shlex.split(cmd)
        assert tokens[0] == "/home/alice/it's/python"


class TestCodexTargetAtomicWrite:
    """F3: CodexTarget.register / unregister must write atomically.

    The original code called config_path.write_text() directly, which
    leaves a truncated file on a mid-write crash.  The fix routes all
    writes through _atomic_write (tmp-then-os.replace).

    We verify atomicity indirectly by asserting:
    - No leftover .mneme-tmp sibling after a normal register/unregister.
    - The resulting file content is correct after register and unregister.
    """

    def test_register_no_tmp_leftover(self, tmp_path: Path) -> None:
        cfg = tmp_path / "config.toml"
        target = CodexTarget(config_path=cfg)
        target.register(tmp_path / "vault")
        leftover = list(tmp_path.glob("*.mneme-tmp"))
        assert leftover == [], f"tmp file not cleaned up: {leftover}"

    def test_register_content_correct(self, tmp_path: Path) -> None:
        cfg = tmp_path / "config.toml"
        target = CodexTarget(config_path=cfg)
        target.register(tmp_path / "vault")
        text = cfg.read_text(encoding="utf-8")
        assert "[mcp_servers.mneme]" in text
        assert 'command = "mneme-mcp"' in text

    def test_unregister_no_tmp_leftover(self, tmp_path: Path) -> None:
        cfg = tmp_path / "config.toml"
        target = CodexTarget(config_path=cfg)
        target.register(tmp_path / "vault")
        target.unregister()
        leftover = list(tmp_path.glob("*.mneme-tmp"))
        assert leftover == [], f"tmp file not cleaned up: {leftover}"

    def test_unregister_removes_block(self, tmp_path: Path) -> None:
        cfg = tmp_path / "config.toml"
        target = CodexTarget(config_path=cfg)
        target.register(tmp_path / "vault")
        target.unregister()
        text = cfg.read_text(encoding="utf-8")
        assert "[mcp_servers.mneme]" not in text

    def test_register_preserves_existing_content(self, tmp_path: Path) -> None:
        cfg = tmp_path / "config.toml"
        cfg.write_text('model = "gpt-5"\n', encoding="utf-8")
        target = CodexTarget(config_path=cfg)
        target.register(tmp_path / "vault")
        text = cfg.read_text(encoding="utf-8")
        assert 'model = "gpt-5"' in text
        assert "[mcp_servers.mneme]" in text
