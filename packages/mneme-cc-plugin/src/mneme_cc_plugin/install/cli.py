"""``mneme`` CLI: three-tier install orchestrator.

Subcommands:

  mneme install      pip + npm + settings.json + vault scaffold + index
  mneme upgrade      change profile in place, rebuild indexes only
  mneme uninstall    remove hooks and MCP entry from settings.json
  mneme doctor       print environment status, no mutation
  mneme version      print package version

The orchestrator separates "what to run" from "how to run it" by
routing every subprocess call through an injectable ``CommandRunner``.
Tests can supply a fake runner and assert on the call log without
spawning real processes.

Subprocess installs (pip, npm, docker check) are run with explicit
argument lists so user-supplied data is never shell-interpolated.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Sequence

import click

from .. import __version__
from .settings import (
    SettingsMutationError,
    add_hook,
    add_mcp_server,
    read_settings,
    remove_hooks,
    remove_mcp_server,
    write_settings,
)

PROFILES = ("lite", "standard", "full")
DEFAULT_PROFILE = "lite"
MNEME_TAG = "mneme"
MCP_SERVER_NAME = "mneme"

PROFILE_EXTRAS: dict[str, list[str]] = {
    "lite": [],
    "standard": ["mneme-core[standard]"],
    "full": ["mneme-core[full]"],
}

HOOK_TIMEOUTS_MS: dict[str, int] = {
    "PostToolUse": 1_000,
    "SessionStart": 800,
    "Stop": 2_000,
    "PreCompact": 500,
    "SessionEnd": 800,
}

HOOK_MODULES: dict[str, str] = {
    "PostToolUse": "mneme_cc_plugin.hooks.post_tool_use",
    "SessionStart": "mneme_cc_plugin.hooks.session_start",
    "Stop": "mneme_cc_plugin.hooks.stop",
    "PreCompact": "mneme_cc_plugin.hooks.pre_compact",
    "SessionEnd": "mneme_cc_plugin.hooks.session_end",
}

HOOK_MATCHERS: dict[str, str | None] = {
    "PostToolUse": "Edit|Write|Bash|Task|MultiEdit",
}


@dataclass
class CommandResult:
    """Captured outcome of a subprocess call."""

    args: tuple[str, ...]
    returncode: int
    stdout: str
    stderr: str


CommandRunner = Callable[[Sequence[str]], CommandResult]


def real_runner(args: Sequence[str]) -> CommandResult:
    """Default runner that spawns the subprocess for real."""
    try:
        proc = subprocess.run(
            list(args),
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=False,
        )
        return CommandResult(
            args=tuple(args),
            returncode=proc.returncode,
            stdout=proc.stdout,
            stderr=proc.stderr,
        )
    except FileNotFoundError as exc:
        return CommandResult(
            args=tuple(args),
            returncode=127,
            stdout="",
            stderr=str(exc),
        )


@dataclass
class InstallerConfig:
    """Inputs for an install run."""

    profile: str
    vault_root: Path
    settings_path: Path
    backup_dir: Path
    dry_run: bool = False


@dataclass
class Installer:
    """Stateful orchestrator. Methods can be called individually for testing."""

    config: InstallerConfig
    runner: CommandRunner = field(default=real_runner)
    interpreter: list[str] = field(default_factory=list)
    log: list[str] = field(default_factory=list)

    def _say(self, msg: str) -> None:
        self.log.append(msg)
        click.echo(msg)

    def detect_interpreter(self) -> list[str]:
        """Pick a stable Python interpreter command for hook invocation.

        Windows: ``py -3`` (Python launcher) avoids the App Execution
        Alias stub that comes with the system. POSIX: ``python3``.
        Tests inject this directly through the ``interpreter`` field.
        """
        if self.interpreter:
            return self.interpreter
        if sys.platform == "win32":
            self.interpreter = ["py", "-3"]
        else:
            self.interpreter = ["python3"]
        return self.interpreter

    def install_python_deps(self) -> None:
        extras = PROFILE_EXTRAS[self.config.profile]
        if not extras:
            self._say(f"pip: profile '{self.config.profile}' has no extras to install")
            return
        if self.config.dry_run:
            self._say(f"pip (dry-run): would install {extras}")
            return
        cmd = [sys.executable, "-m", "pip", "install", "--upgrade", *extras]
        self._say(f"pip: {' '.join(cmd)}")
        res = self.runner(cmd)
        if res.returncode != 0:
            raise click.ClickException(
                f"pip install failed (exit {res.returncode}): {res.stderr[:400]}"
            )

    def install_node_deps(self) -> None:
        if self.config.dry_run:
            self._say("npm (dry-run): would install -g mneme-mcp")
            return
        cmd = ["npm", "install", "-g", "mneme-mcp"]
        self._say(f"npm: {' '.join(cmd)}")
        res = self.runner(cmd)
        if res.returncode != 0:
            raise click.ClickException(
                f"npm install failed (exit {res.returncode}): {res.stderr[:400]}"
            )

    def init_vault(self) -> None:
        marker = self.config.vault_root / ".mneme"
        marker.mkdir(parents=True, exist_ok=True)
        config_toml = marker / "config.toml"
        if not config_toml.exists():
            config_toml.write_text(
                f'profile = "{self.config.profile}"\n'
                f"schema_version = 1\n",
                encoding="utf-8",
            )
        self._say(f"vault: marker created at {marker}")

    def register_hooks(self) -> None:
        interp = self.detect_interpreter()
        if not self.config.settings_path.exists():
            raise click.ClickException(
                f"Claude Code settings.json not found at {self.config.settings_path}. "
                "Start Claude Code at least once before running mneme install."
            )
        data = read_settings(self.config.settings_path)
        added_count = 0
        for event, module in HOOK_MODULES.items():
            command = " ".join([*interp, "-m", module])
            added = add_hook(
                data,
                event,
                command,
                matcher=HOOK_MATCHERS.get(event),
                timeout_ms=HOOK_TIMEOUTS_MS[event],
                tag=MNEME_TAG,
            )
            if added:
                added_count += 1
        add_mcp_server(
            data,
            MCP_SERVER_NAME,
            command="mneme-mcp",
            args=[],
            env={"MNEME_VAULT": str(self.config.vault_root)},
        )
        write_settings(
            self.config.settings_path,
            data,
            backup_dir=self.config.backup_dir,
            reason="mneme-install",
        )
        self._say(f"settings.json: {added_count} hook entries added, MCP wired")

    def unregister(self) -> None:
        if not self.config.settings_path.exists():
            self._say("settings.json: nothing to remove")
            return
        data = read_settings(self.config.settings_path)
        removed = remove_hooks(data, tag=MNEME_TAG)
        mcp_removed = remove_mcp_server(data, MCP_SERVER_NAME)
        write_settings(
            self.config.settings_path,
            data,
            backup_dir=self.config.backup_dir,
            reason="mneme-uninstall",
        )
        self._say(
            f"settings.json: {removed} hook entries removed, "
            f"MCP entry {'removed' if mcp_removed else 'not present'}"
        )

    def doctor(self) -> dict[str, object]:
        report: dict[str, object] = {
            "platform": sys.platform,
            "python": sys.version.split()[0],
            "node_present": shutil.which("node") is not None,
            "npm_present": shutil.which("npm") is not None,
            "docker_present": shutil.which("docker") is not None,
            "vault_root": str(self.config.vault_root),
            "vault_marker_exists": (self.config.vault_root / ".mneme").exists(),
            "settings_path": str(self.config.settings_path),
            "settings_exists": self.config.settings_path.exists(),
            "profile": self.config.profile,
            "interpreter": self.detect_interpreter(),
        }
        return report


def _default_settings_path() -> Path:
    return Path.home() / ".claude" / "settings.json"


def _default_backup_dir() -> Path:
    return Path.home() / ".claude" / "mneme-backups"


def _default_vault_root() -> Path:
    return Path.home() / "mneme-vault"


@click.group(help="mneme install + lifecycle CLI.")
@click.version_option(__version__, prog_name="mneme")
def cli() -> None:  # pragma: no cover - dispatcher
    pass


@cli.command(help="Install mneme into Claude Code.")
@click.option(
    "--profile",
    type=click.Choice(PROFILES),
    default=DEFAULT_PROFILE,
    show_default=True,
)
@click.option(
    "--vault",
    "vault_root",
    type=click.Path(file_okay=False, path_type=Path),
    default=None,
    help="Vault root. Defaults to ~/mneme-vault.",
)
@click.option(
    "--settings",
    "settings_path",
    type=click.Path(dir_okay=False, path_type=Path),
    default=None,
    help="Claude Code settings.json path. Defaults to ~/.claude/settings.json.",
)
@click.option(
    "--backup-dir",
    "backup_dir",
    type=click.Path(file_okay=False, path_type=Path),
    default=None,
    help="Where to keep settings.json backups. Defaults to ~/.claude/mneme-backups.",
)
@click.option(
    "--skip-python", is_flag=True, help="Skip pip install (for editable monorepo dev)."
)
@click.option(
    "--skip-node", is_flag=True, help="Skip npm install (for editable monorepo dev)."
)
@click.option("--dry-run", is_flag=True, help="Plan the install but make no changes.")
def install(
    profile: str,
    vault_root: Path | None,
    settings_path: Path | None,
    backup_dir: Path | None,
    skip_python: bool,
    skip_node: bool,
    dry_run: bool,
) -> None:
    cfg = InstallerConfig(
        profile=profile,
        vault_root=(vault_root or _default_vault_root()).expanduser().resolve(),
        settings_path=(settings_path or _default_settings_path()).expanduser().resolve(),
        backup_dir=(backup_dir or _default_backup_dir()).expanduser().resolve(),
        dry_run=dry_run,
    )
    inst = Installer(config=cfg)
    if not skip_python:
        inst.install_python_deps()
    if not skip_node:
        inst.install_node_deps()
    inst.init_vault()
    if not dry_run:
        inst.register_hooks()
    inst._say(f"mneme install complete (profile={profile}, vault={cfg.vault_root})")


@cli.command(help="Upgrade profile or refresh indexes.")
@click.option(
    "--profile",
    type=click.Choice(PROFILES),
    required=True,
)
def upgrade(profile: str) -> None:
    cfg = InstallerConfig(
        profile=profile,
        vault_root=_default_vault_root(),
        settings_path=_default_settings_path(),
        backup_dir=_default_backup_dir(),
    )
    inst = Installer(config=cfg)
    inst.install_python_deps()
    inst._say(f"upgraded to profile={profile}")


@cli.command(help="Remove mneme hooks and MCP entry from settings.json.")
@click.option(
    "--settings",
    "settings_path",
    type=click.Path(dir_okay=False, path_type=Path),
    default=None,
)
@click.option(
    "--backup-dir",
    "backup_dir",
    type=click.Path(file_okay=False, path_type=Path),
    default=None,
)
def uninstall(
    settings_path: Path | None,
    backup_dir: Path | None,
) -> None:
    cfg = InstallerConfig(
        profile=DEFAULT_PROFILE,
        vault_root=_default_vault_root(),
        settings_path=(settings_path or _default_settings_path()).expanduser().resolve(),
        backup_dir=(backup_dir or _default_backup_dir()).expanduser().resolve(),
    )
    try:
        Installer(config=cfg).unregister()
    except SettingsMutationError as exc:
        raise click.ClickException(str(exc)) from exc


@cli.command(help="Print environment diagnostic without mutation.")
@click.option(
    "--vault",
    "vault_root",
    type=click.Path(file_okay=False, path_type=Path),
    default=None,
)
@click.option(
    "--settings",
    "settings_path",
    type=click.Path(dir_okay=False, path_type=Path),
    default=None,
)
@click.option(
    "--profile",
    type=click.Choice(PROFILES),
    default=DEFAULT_PROFILE,
)
def doctor(
    vault_root: Path | None,
    settings_path: Path | None,
    profile: str,
) -> None:
    cfg = InstallerConfig(
        profile=profile,
        vault_root=(vault_root or _default_vault_root()).expanduser().resolve(),
        settings_path=(settings_path or _default_settings_path()).expanduser().resolve(),
        backup_dir=_default_backup_dir(),
    )
    report = Installer(config=cfg).doctor()
    click.echo(json.dumps(report, indent=2, default=str))


def main() -> None:
    cli(prog_name="mneme")


if __name__ == "__main__":  # pragma: no cover
    main()
