"""``mneme`` CLI: three-tier install orchestrator.

Subcommands:

  mneme install      pip + npm + settings.json + vault scaffold + index
  mneme upgrade      change profile in place, rebuild indexes only
  mneme uninstall    remove hooks and MCP entry from settings.json
  mneme doctor       print environment status, no mutation
  mneme hook         dispatch Claude Code or Codex lifecycle hooks
  mneme index        FTS5 index maintenance from mneme-core
  mneme kg           full-profile knowledge-graph worker from mneme-core
  mneme compress     opt-in compression lifecycle from mneme-core
  mneme patterns     reusable action-pattern memory from mneme-core
  mneme trajectory   per-session trajectory recorder from mneme-core
  mneme audit        token consumption audit from mneme-core
  mneme audit-log    privacy redaction audit reader from mneme-core
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
import os
import shlex
import shutil
import subprocess
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path

import click
import mneme_core.cli as core_cli
from mneme_core.distill.audit import cli as audit_cli

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

# Hook timeouts are SECONDS, matching the Claude Code settings.json hook
# schema. A prior version stored these as milliseconds, which the schema
# then read as 1000-2000 SECONDS and could hang the editor on a wedged
# hook. The values are safety ceilings above each hook's internal
# deadlines, not p95 targets: Stop can legitimately wait on the
# session-log lock (5s) plus a git status (3s), so its ceiling is 10s.
# These must stay in sync with the native plugin manifest hooks/hooks.json
# (enforced by tests/unit/test_hook_timeouts_consistent.py).
HOOK_TIMEOUTS_S: dict[str, int] = {
    "PostToolUse": 5,
    "SessionStart": 5,
    "Stop": 10,
    "PreCompact": 5,
    "SessionEnd": 10,
}

# Map PascalCase hook events to their `mneme hook <event>` console-script
# subcommand. Wiring hooks through the installed console script rather
# than `python3 -m ...` is what makes them work under a pipx isolated
# venv, where a bare interpreter on PATH cannot import the plugin package.
HOOK_EVENT_COMMAND: dict[str, str] = {
    "PostToolUse": "post-tool-use",
    "SessionStart": "session-start",
    "Stop": "stop",
    "PreCompact": "pre-compact",
    "SessionEnd": "session-end",
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

    def _hook_command(self, event: str) -> str:
        """Build the settings.json command string for a lifecycle hook.

        Prefer the installed ``mneme`` console script (``mneme hook
        <event>``) so hooks fire correctly under a pipx isolated venv,
        where a bare ``python3``/``py`` on PATH resolves to a system
        interpreter that cannot import the plugin package. Fall back to
        the absolute install-time interpreter running the install CLI as
        a module, which still resolves the right environment by path.
        """
        sub = HOOK_EVENT_COMMAND[event]
        if shutil.which("mneme"):
            return f"mneme hook {sub}"
        # On Windows, double-quoting the interpreter path is correct shell
        # syntax for paths with spaces; quotes inside the path are not
        # supported by cmd.exe so we accept that limitation. On POSIX we
        # use shlex.quote which handles both spaces and embedded quotes.
        if sys.platform == "win32":
            quoted = f'"{sys.executable}"'
        else:
            quoted = shlex.quote(sys.executable)
        return f"{quoted} -m mneme_cc_plugin.install.cli hook {sub}"

    def register_hooks(self) -> None:
        if not self.config.settings_path.exists():
            raise click.ClickException(
                f"Claude Code settings.json not found at {self.config.settings_path}. "
                "Start Claude Code at least once before running mneme install."
            )
        data = read_settings(self.config.settings_path)
        added_count = 0
        for event in HOOK_MODULES:
            command = self._hook_command(event)
            added = add_hook(
                data,
                event,
                command,
                matcher=HOOK_MATCHERS.get(event),
                timeout_s=HOOK_TIMEOUTS_S[event],
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
        codex_config = _default_codex_config_path()
        report["codex_present"] = shutil.which("codex") is not None
        report["codex_config_path"] = str(codex_config)
        report["codex_config_exists"] = codex_config.exists()
        return report


def _default_settings_path() -> Path:
    return Path.home() / ".claude" / "settings.json"


def _default_backup_dir() -> Path:
    return Path.home() / ".claude" / "mneme-backups"


def _default_vault_root() -> Path:
    return Path.home() / "mneme-vault"


def _default_codex_config_path() -> Path:
    return Path.home() / ".codex" / "config.toml"


CODEX_BLOCK_START = "# >>> mneme (managed) >>>"
CODEX_BLOCK_END = "# <<< mneme (managed) <<<"


def _strip_managed_block(text: str, start: str, end: str) -> str:
    """Drop the inclusive ``start``..``end`` marker block from ``text``."""
    out: list[str] = []
    skipping = False
    for line in text.splitlines(keepends=True):
        stripped = line.strip()
        if stripped == start:
            skipping = True
            continue
        if stripped == end:
            skipping = False
            continue
        if not skipping:
            out.append(line)
    return "".join(out)


@dataclass
class CodexTarget:
    """Wire mneme's MCP server into ``~/.codex/config.toml``.

    Codex reads MCP servers from a ``[mcp_servers.<name>]`` table. The
    entry is bracketed by managed-block sentinels so uninstall removes
    exactly mneme's lines and leaves the rest of the user's config
    untouched, with no TOML parser or extra dependency. Codex hooks and
    skills ship via the Codex marketplace plugin
    (``packages/mneme-codex-plugin``), not this writer.
    """

    config_path: Path

    def _block(self, vault_root: Path) -> str:
        return (
            f"{CODEX_BLOCK_START}\n"
            "[mcp_servers.mneme]\n"
            'command = "mneme-mcp"\n'
            "args = []\n"
            "[mcp_servers.mneme.env]\n"
            f'MNEME_VAULT = "{vault_root.as_posix()}"\n'
            f"{CODEX_BLOCK_END}\n"
        )

    def _atomic_write(self, content: str) -> None:
        """Write ``content`` to ``config_path`` via a sibling tmp file.

        Uses ``os.replace`` for an atomic overwrite so a crash mid-write
        cannot truncate the user's Codex config. Mirrors the discipline
        in ``settings.write_settings`` / ``atomic_write_text``.
        """
        self.config_path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = self.config_path.with_suffix(self.config_path.suffix + ".mneme-tmp")
        try:
            tmp_path.write_text(content, encoding="utf-8")
            os.replace(tmp_path, self.config_path)
        except BaseException:
            tmp_path.unlink(missing_ok=True)
            raise

    def register(self, vault_root: Path) -> str:
        existing = (
            self.config_path.read_text(encoding="utf-8")
            if self.config_path.exists()
            else ""
        )
        if CODEX_BLOCK_START in existing:
            return "codex: mneme MCP block already present in config.toml"
        prefix = existing
        if prefix and not prefix.endswith("\n"):
            prefix += "\n"
        if prefix:
            prefix += "\n"
        self._atomic_write(prefix + self._block(vault_root))
        return "codex: mneme MCP server registered in config.toml"

    def unregister(self) -> str:
        if not self.config_path.exists():
            return "codex: config.toml not found, nothing to remove"
        text = self.config_path.read_text(encoding="utf-8")
        if CODEX_BLOCK_START not in text:
            return "codex: no mneme block present in config.toml"
        self._atomic_write(
            _strip_managed_block(text, CODEX_BLOCK_START, CODEX_BLOCK_END)
        )
        return "codex: mneme MCP block removed from config.toml"


@click.group(help="mneme install + lifecycle CLI.")
@click.version_option(__version__, prog_name="mneme")
def cli() -> None:  # pragma: no cover - dispatcher
    pass


@cli.command(help="Install mneme into Claude Code and/or Codex.")
@click.option(
    "--profile",
    type=click.Choice(PROFILES),
    default=DEFAULT_PROFILE,
    show_default=True,
)
@click.option(
    "--upgrade-profile",
    type=click.Choice(PROFILES),
    default=None,
    help="Compatibility alias for --profile when upgrading an existing vault.",
)
@click.option(
    "--client",
    type=click.Choice(["claude-code", "codex", "all"]),
    default="claude-code",
    show_default=True,
    help="Which client(s) to wire mneme into.",
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
    "--codex-config",
    "codex_config",
    type=click.Path(dir_okay=False, path_type=Path),
    default=None,
    help="Codex config.toml path. Defaults to ~/.codex/config.toml.",
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
    upgrade_profile: str | None,
    client: str,
    vault_root: Path | None,
    settings_path: Path | None,
    codex_config: Path | None,
    backup_dir: Path | None,
    skip_python: bool,
    skip_node: bool,
    dry_run: bool,
) -> None:
    if upgrade_profile is not None:
        profile = upgrade_profile
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
    if dry_run:
        inst._say(f"vault (dry-run): would create marker at {cfg.vault_root / '.mneme'}")
    else:
        inst.init_vault()
        if client in ("claude-code", "all"):
            if cfg.settings_path.exists():
                inst.register_hooks()
            elif client == "claude-code":
                raise click.ClickException(
                    f"Claude Code settings.json not found at {cfg.settings_path}. "
                    "Start Claude Code once, or install for Codex with --client=codex."
                )
            else:
                inst._say("claude-code: settings.json not found, skipping (client=all)")
        if client in ("codex", "all"):
            target = CodexTarget(
                config_path=(codex_config or _default_codex_config_path())
                .expanduser()
                .resolve()
            )
            inst._say(target.register(cfg.vault_root))
    inst._say(
        f"mneme install complete (profile={profile}, client={client}, "
        f"vault={cfg.vault_root})"
    )


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


@cli.command(help="Remove mneme from Claude Code and/or Codex.")
@click.option(
    "--client",
    type=click.Choice(["claude-code", "codex", "all"]),
    default="claude-code",
    show_default=True,
    help="Which client(s) to remove mneme from.",
)
@click.option(
    "--settings",
    "settings_path",
    type=click.Path(dir_okay=False, path_type=Path),
    default=None,
)
@click.option(
    "--codex-config",
    "codex_config",
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
    client: str,
    settings_path: Path | None,
    codex_config: Path | None,
    backup_dir: Path | None,
) -> None:
    if client in ("claude-code", "all"):
        cfg = InstallerConfig(
            profile=DEFAULT_PROFILE,
            vault_root=_default_vault_root(),
            settings_path=(settings_path or _default_settings_path())
            .expanduser()
            .resolve(),
            backup_dir=(backup_dir or _default_backup_dir()).expanduser().resolve(),
        )
        try:
            Installer(config=cfg).unregister()
        except SettingsMutationError as exc:
            raise click.ClickException(str(exc)) from exc
    if client in ("codex", "all"):
        target = CodexTarget(
            config_path=(codex_config or _default_codex_config_path())
            .expanduser()
            .resolve()
        )
        click.echo(target.unregister())


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


@cli.command("version", help="Print mneme package version.")
def version_cmd() -> None:
    click.echo(__version__)


@cli.command(
    help=(
        "Dispatch a lifecycle hook event. Reads the event JSON on stdin "
        "and writes the hook response on stdout. The native plugin "
        "hooks.json (Claude Code and Codex) call this so a single "
        "OS-agnostic command works on every platform and client."
    )
)
@click.argument(
    "event",
    type=click.Choice(
        ["session-start", "post-tool-use", "stop", "pre-compact", "session-end"]
    ),
)
def hook(event: str) -> None:
    # Lazy import: the hook modules pull in mneme_core staging, distill,
    # and kg, which install/upgrade/uninstall/doctor never need. Importing
    # only when a hook actually fires keeps CLI startup light.
    from ..hooks import (
        post_tool_use,
        pre_compact,
        session_end,
        session_start,
        stop,
    )

    mains: dict[str, Callable[[], int]] = {
        "session-start": session_start.main,
        "post-tool-use": post_tool_use.main,
        "stop": stop.main,
        "pre-compact": pre_compact.main,
        "session-end": session_end.main,
    }
    sys.exit(mains[event]())


def _register_core_commands() -> None:
    """Expose the vault-operation CLI under the plugin-owned console script.

    Both mneme-core and mneme-cc-plugin historically published a
    ``mneme`` console script. In a normal install the plugin entry
    point wins, which hid the public vault commands documented in the
    README. Re-registering the core Click groups here keeps one user
    contract without duplicating the implementation.
    """

    cli.add_command(core_cli.index, "index")
    cli.add_command(core_cli.kg, "kg")
    cli.add_command(core_cli.compress, "compress")
    cli.add_command(core_cli.patterns_group, "patterns")
    cli.add_command(core_cli.trajectory_group, "trajectory")
    cli.add_command(audit_cli, "audit")
    cli.add_command(core_cli.audit_log, "audit-log")


_register_core_commands()


def main() -> None:
    cli(prog_name="mneme")


if __name__ == "__main__":  # pragma: no cover
    main()
