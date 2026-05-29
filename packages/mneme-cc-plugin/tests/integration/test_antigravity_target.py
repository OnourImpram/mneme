"""Integration tests for AntigravityTarget and ``mneme install --client antigravity``.

Covers:
- register() writes gemini-extension.json, hooks/hooks.json, skills, GEMINI.md.
- The installed manifest has the vault path in env.MNEME_VAULT (concrete, not
  the portability variable used by the in-repo template).
- unregister() removes the extension directory.
- register() is idempotent (second call returns "already present").
- Hook command strings in the installed hooks.json match the Codex plugin's
  command strings exactly (hook-dispatch parity).
- ``mneme install --client antigravity`` end-to-end writes the extension.
- Existing install tests are unaffected (existing clients untouched).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from mneme_cc_plugin.install.cli import (
    AntigravityTarget,
    cli,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_CODEX_PLUGIN_ROOT = (
    Path(__file__).resolve().parents[4] / "packages" / "mneme-codex-plugin"
)


def _load_json(path: Path) -> dict[str, object]:
    return json.loads(path.read_text(encoding="utf-8"))  # type: ignore[return-value]


def _all_hook_commands(hooks_json: dict[str, object]) -> list[str]:
    """Flatten all command strings from a hooks.json payload."""
    commands: list[str] = []
    hooks_map = hooks_json.get("hooks", {})
    assert isinstance(hooks_map, dict)
    for handlers in hooks_map.values():
        assert isinstance(handlers, list)
        for h in handlers:
            assert isinstance(h, dict)
            for entry in h.get("hooks", []):
                assert isinstance(entry, dict)
                cmd = entry.get("command")
                if isinstance(cmd, str):
                    commands.append(cmd)
    return commands


# ---------------------------------------------------------------------------
# AntigravityTarget unit tests
# ---------------------------------------------------------------------------


class TestAntigravityTargetRegister:
    def test_register_writes_manifest(self, tmp_path: Path) -> None:
        extensions_dir = tmp_path / "extensions"
        target = AntigravityTarget(extensions_dir=extensions_dir)
        vault = tmp_path / "vault"
        msg = target.register(vault)
        assert "registered" in msg
        manifest_path = extensions_dir / "mneme" / "gemini-extension.json"
        assert manifest_path.is_file()
        manifest = _load_json(manifest_path)
        assert manifest["name"] == "mneme"
        assert "version" in manifest
        assert "description" in manifest
        assert manifest["contextFileName"] == "GEMINI.md"

    def test_register_sets_vault_in_env(self, tmp_path: Path) -> None:
        extensions_dir = tmp_path / "extensions"
        target = AntigravityTarget(extensions_dir=extensions_dir)
        vault = tmp_path / "my-vault"
        target.register(vault)
        manifest = _load_json(extensions_dir / "mneme" / "gemini-extension.json")
        mcp = manifest["mcpServers"]
        assert isinstance(mcp, dict)
        mneme_server = mcp["mneme"]
        assert isinstance(mneme_server, dict)
        env = mneme_server["env"]
        assert isinstance(env, dict)
        # Installed copy uses the concrete path, not the ${MNEME_VAULT} placeholder.
        assert env["MNEME_VAULT"] == str(vault)

    def test_register_writes_hooks_json(self, tmp_path: Path) -> None:
        extensions_dir = tmp_path / "extensions"
        target = AntigravityTarget(extensions_dir=extensions_dir)
        target.register(tmp_path / "vault")
        hooks_path = extensions_dir / "mneme" / "hooks" / "hooks.json"
        assert hooks_path.is_file()
        hooks = _load_json(hooks_path)
        assert "hooks" in hooks
        hooks_map = hooks["hooks"]
        assert isinstance(hooks_map, dict)
        for event in ("SessionStart", "PostToolUse", "Stop", "PreCompact"):
            assert event in hooks_map, f"Missing event: {event}"

    def test_register_writes_skills(self, tmp_path: Path) -> None:
        extensions_dir = tmp_path / "extensions"
        target = AntigravityTarget(extensions_dir=extensions_dir)
        target.register(tmp_path / "vault")
        ext_dir = extensions_dir / "mneme"
        for skill in ("mneme-prime", "mneme-search"):
            skill_md = ext_dir / "skills" / skill / "SKILL.md"
            assert skill_md.is_file(), f"Missing: {skill_md}"
            text = skill_md.read_text(encoding="utf-8")
            assert text.startswith("---\n"), f"{skill} SKILL.md lacks frontmatter"
            assert "name:" in text
            assert "description:" in text

    def test_register_writes_gemini_md(self, tmp_path: Path) -> None:
        extensions_dir = tmp_path / "extensions"
        target = AntigravityTarget(extensions_dir=extensions_dir)
        target.register(tmp_path / "vault")
        gemini_md = extensions_dir / "mneme" / "GEMINI.md"
        assert gemini_md.is_file()
        assert gemini_md.read_text(encoding="utf-8").strip()

    def test_register_is_idempotent(self, tmp_path: Path) -> None:
        extensions_dir = tmp_path / "extensions"
        target = AntigravityTarget(extensions_dir=extensions_dir)
        vault = tmp_path / "vault"
        first = target.register(vault)
        assert "registered" in first
        second = target.register(vault)
        assert "already present" in second
        # Only one manifest on disk.
        manifests = list((extensions_dir / "mneme").glob("gemini-extension.json"))
        assert len(manifests) == 1

    def test_register_no_tmp_leftover(self, tmp_path: Path) -> None:
        extensions_dir = tmp_path / "extensions"
        target = AntigravityTarget(extensions_dir=extensions_dir)
        target.register(tmp_path / "vault")
        leftovers = list((extensions_dir / "mneme").rglob("*.mneme-tmp"))
        assert leftovers == [], f"tmp files not cleaned up: {leftovers}"


class TestAntigravityTargetUnregister:
    def test_unregister_removes_extension_dir(self, tmp_path: Path) -> None:
        extensions_dir = tmp_path / "extensions"
        target = AntigravityTarget(extensions_dir=extensions_dir)
        target.register(tmp_path / "vault")
        assert (extensions_dir / "mneme").exists()
        msg = target.unregister()
        assert "removed" in msg
        assert not (extensions_dir / "mneme").exists()

    def test_unregister_when_not_present(self, tmp_path: Path) -> None:
        extensions_dir = tmp_path / "extensions"
        target = AntigravityTarget(extensions_dir=extensions_dir)
        msg = target.unregister()
        assert "not found" in msg or "nothing to remove" in msg


# ---------------------------------------------------------------------------
# Hook-dispatch parity: antigravity must use the same command strings as Codex
# ---------------------------------------------------------------------------


class TestHookCommandParity:
    """The antigravity hooks.json command strings must match the Codex plugin's."""

    def test_hook_commands_match_codex(self, tmp_path: Path) -> None:
        extensions_dir = tmp_path / "extensions"
        target = AntigravityTarget(extensions_dir=extensions_dir)
        target.register(tmp_path / "vault")

        ag_hooks = _load_json(extensions_dir / "mneme" / "hooks" / "hooks.json")
        codex_hooks = _load_json(_CODEX_PLUGIN_ROOT / "hooks" / "hooks.json")

        ag_cmds = sorted(_all_hook_commands(ag_hooks))
        codex_cmds = sorted(_all_hook_commands(codex_hooks))

        assert ag_cmds == codex_cmds, (
            f"Hook command parity failure.\n"
            f"  antigravity: {ag_cmds}\n"
            f"  codex:       {codex_cmds}"
        )

    def test_all_hook_commands_are_mneme_hook_pattern(self, tmp_path: Path) -> None:
        """Every command must be 'mneme hook <event>'."""
        import re

        pattern = re.compile(
            r"^mneme hook (session-start|post-tool-use|stop|pre-compact)$"
        )
        extensions_dir = tmp_path / "extensions"
        target = AntigravityTarget(extensions_dir=extensions_dir)
        target.register(tmp_path / "vault")
        hooks = _load_json(extensions_dir / "mneme" / "hooks" / "hooks.json")
        for cmd in _all_hook_commands(hooks):
            assert pattern.match(cmd), f"Command {cmd!r} does not match pattern"


# ---------------------------------------------------------------------------
# End-to-end CLI: ``mneme install --client antigravity``
# ---------------------------------------------------------------------------


class TestInstallCliAntigravity:
    @pytest.fixture
    def runner(self) -> CliRunner:
        return CliRunner()

    def test_install_antigravity_writes_extension(
        self, runner: CliRunner, tmp_path: Path
    ) -> None:
        extensions_dir = tmp_path / "gemini" / "extensions"
        result = runner.invoke(
            cli,
            [
                "install",
                "--client", "antigravity",
                "--vault", str(tmp_path / "vault"),
                "--antigravity-extensions-dir", str(extensions_dir),
                "--skip-python",
                "--skip-node",
            ],
        )
        assert result.exit_code == 0, result.output
        assert (extensions_dir / "mneme" / "gemini-extension.json").is_file()
        assert (extensions_dir / "mneme" / "hooks" / "hooks.json").is_file()
        assert (extensions_dir / "mneme" / "GEMINI.md").is_file()

    def test_install_all_includes_antigravity(
        self, runner: CliRunner, tmp_path: Path
    ) -> None:
        """--client all must also wire Antigravity."""
        settings = tmp_path / ".claude" / "settings.json"
        settings.parent.mkdir(parents=True)
        settings.write_text("{}", encoding="utf-8")
        extensions_dir = tmp_path / "gemini" / "extensions"
        result = runner.invoke(
            cli,
            [
                "install",
                "--client", "all",
                "--vault", str(tmp_path / "vault"),
                "--settings", str(settings),
                "--backup-dir", str(tmp_path / "bak"),
                "--antigravity-extensions-dir", str(extensions_dir),
                "--skip-python",
                "--skip-node",
            ],
        )
        assert result.exit_code == 0, result.output
        assert (extensions_dir / "mneme" / "gemini-extension.json").is_file()

    def test_uninstall_antigravity_removes_extension(
        self, runner: CliRunner, tmp_path: Path
    ) -> None:
        extensions_dir = tmp_path / "gemini" / "extensions"
        # Install first.
        install = runner.invoke(
            cli,
            [
                "install",
                "--client", "antigravity",
                "--vault", str(tmp_path / "vault"),
                "--antigravity-extensions-dir", str(extensions_dir),
                "--skip-python",
                "--skip-node",
            ],
        )
        assert install.exit_code == 0, install.output
        assert (extensions_dir / "mneme").exists()

        # Uninstall.
        out = runner.invoke(
            cli,
            [
                "uninstall",
                "--client", "antigravity",
                "--antigravity-extensions-dir", str(extensions_dir),
            ],
        )
        assert out.exit_code == 0, out.output
        assert not (extensions_dir / "mneme").exists()

    def test_install_antigravity_vault_path_in_manifest(
        self, runner: CliRunner, tmp_path: Path
    ) -> None:
        vault = tmp_path / "my-vault"
        extensions_dir = tmp_path / "ext"
        result = runner.invoke(
            cli,
            [
                "install",
                "--client", "antigravity",
                "--vault", str(vault),
                "--antigravity-extensions-dir", str(extensions_dir),
                "--skip-python",
                "--skip-node",
            ],
        )
        assert result.exit_code == 0, result.output
        manifest = _load_json(extensions_dir / "mneme" / "gemini-extension.json")
        mcp = manifest["mcpServers"]
        assert isinstance(mcp, dict)
        env = mcp["mneme"]["env"]  # type: ignore[index]
        assert isinstance(env, dict)
        assert env["MNEME_VAULT"] == str(vault.resolve())
