"""Integration tests for GenericMcpTarget and ``mneme install --client mcp``.

Covers:
- register() into a missing file → creates a valid stanza.
- register() into an existing JSON config with other servers → adds mneme and
  preserves every other key and server entry.
- register() on a non-JSON file → raises ClickException without clobbering.
- unregister() removes only the mneme entry and preserves other servers.
- register() is idempotent (second call with identical stanza skips rewrite).
- End-to-end: ``mneme install --client mcp --config <tmp>`` succeeds.
- ``mneme install --client mcp`` without ``--config`` exits with an error.
- ``mneme uninstall --client mcp`` without ``--config`` exits with an error.
- Atomic write: no .mneme-tmp leftover after register/unregister.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from mneme_cc_plugin.install.cli import (
    GenericMcpTarget,
    cli,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _load(path: Path) -> dict[str, object]:
    return json.loads(path.read_text(encoding="utf-8"))  # type: ignore[return-value]


# ---------------------------------------------------------------------------
# GenericMcpTarget.register
# ---------------------------------------------------------------------------


class TestGenericMcpTargetRegister:
    def test_missing_file_creates_stanza(self, tmp_path: Path) -> None:
        """register() into a non-existent file creates it with the mneme stanza."""
        cfg = tmp_path / "client" / "mcp.json"
        vault = tmp_path / "vault"
        target = GenericMcpTarget(config_path=cfg)

        msg = target.register(vault)

        assert cfg.is_file()
        assert "registered" in msg
        data = _load(cfg)
        assert "mcpServers" in data
        mneme = data["mcpServers"]["mneme"]  # type: ignore[index]
        assert isinstance(mneme, dict)
        assert mneme["command"] == "mneme-mcp"
        assert mneme["env"]["MNEME_VAULT"] == str(vault)  # type: ignore[index]

    def test_existing_config_adds_mneme_preserves_others(self, tmp_path: Path) -> None:
        """register() merges mneme and leaves other servers and top-level keys intact."""
        cfg = tmp_path / "mcp.json"
        existing: dict[str, object] = {
            "globalSetting": True,
            "mcpServers": {
                "other-tool": {"command": "other-mcp", "env": {}},
            },
        }
        cfg.write_text(json.dumps(existing, indent=2), encoding="utf-8")
        vault = tmp_path / "vault"
        target = GenericMcpTarget(config_path=cfg)

        msg = target.register(vault)

        assert "registered" in msg
        data = _load(cfg)
        # mneme stanza present.
        assert data["mcpServers"]["mneme"]["command"] == "mneme-mcp"  # type: ignore[index]
        # other-tool preserved.
        assert data["mcpServers"]["other-tool"]["command"] == "other-mcp"  # type: ignore[index]
        # top-level key preserved.
        assert data["globalSetting"] is True  # type: ignore[index]

    def test_non_json_file_raises_click_exception(self, tmp_path: Path) -> None:
        """register() on a non-JSON file raises ClickException and does not clobber."""
        cfg = tmp_path / "mcp.json"
        original_content = "this is not json at all\n"
        cfg.write_text(original_content, encoding="utf-8")
        target = GenericMcpTarget(config_path=cfg)

        import click

        with pytest.raises(click.ClickException, match="not valid JSON"):
            target.register(tmp_path / "vault")

        # File must be untouched.
        assert cfg.read_text(encoding="utf-8") == original_content

    def test_non_object_json_raises_click_exception(self, tmp_path: Path) -> None:
        """register() on a JSON array (not an object) raises ClickException."""
        cfg = tmp_path / "mcp.json"
        cfg.write_text("[1, 2, 3]\n", encoding="utf-8")
        target = GenericMcpTarget(config_path=cfg)

        import click

        with pytest.raises(click.ClickException, match="not an object"):
            target.register(tmp_path / "vault")

        # File must be untouched.
        assert cfg.read_text(encoding="utf-8") == "[1, 2, 3]\n"

    def test_register_idempotent_identical_stanza(self, tmp_path: Path) -> None:
        """Second register() with the same vault returns 'already present' and skips write."""
        cfg = tmp_path / "mcp.json"
        vault = tmp_path / "vault"
        target = GenericMcpTarget(config_path=cfg)

        first_msg = target.register(vault)
        assert "registered" in first_msg

        mtime_after_first = cfg.stat().st_mtime

        second_msg = target.register(vault)
        assert "already present" in second_msg

        # File must not have been rewritten.
        assert cfg.stat().st_mtime == mtime_after_first

    def test_register_updates_stanza_when_vault_changes(self, tmp_path: Path) -> None:
        """register() with a different vault_root overwrites the stanza."""
        cfg = tmp_path / "mcp.json"
        vault_a = tmp_path / "vault-a"
        vault_b = tmp_path / "vault-b"
        target = GenericMcpTarget(config_path=cfg)

        target.register(vault_a)
        msg = target.register(vault_b)

        assert "registered" in msg
        data = _load(cfg)
        assert data["mcpServers"]["mneme"]["env"]["MNEME_VAULT"] == str(vault_b)  # type: ignore[index]

    def test_register_no_tmp_leftover(self, tmp_path: Path) -> None:
        """No .mneme-tmp sibling is left after a successful register()."""
        cfg = tmp_path / "mcp.json"
        target = GenericMcpTarget(config_path=cfg)
        target.register(tmp_path / "vault")
        leftovers = list(tmp_path.rglob("*.mneme-tmp"))
        assert leftovers == [], f"tmp files not cleaned up: {leftovers}"


# ---------------------------------------------------------------------------
# GenericMcpTarget.unregister
# ---------------------------------------------------------------------------


class TestGenericMcpTargetUnregister:
    def test_unregister_removes_only_mneme_entry(self, tmp_path: Path) -> None:
        """unregister() removes mneme and leaves all other servers and keys intact."""
        cfg = tmp_path / "mcp.json"
        existing: dict[str, object] = {
            "topLevelKey": "preserved",
            "mcpServers": {
                "mneme": {"command": "mneme-mcp", "env": {"MNEME_VAULT": str(tmp_path)}},
                "another": {"command": "another-mcp"},
            },
        }
        cfg.write_text(json.dumps(existing, indent=2), encoding="utf-8")
        target = GenericMcpTarget(config_path=cfg)

        msg = target.unregister()

        assert "removed" in msg
        data = _load(cfg)
        assert "mneme" not in data["mcpServers"]  # type: ignore[operator]
        assert data["mcpServers"]["another"]["command"] == "another-mcp"  # type: ignore[index]
        assert data["topLevelKey"] == "preserved"  # type: ignore[index]

    def test_unregister_when_file_absent(self, tmp_path: Path) -> None:
        """unregister() on a missing file returns a 'nothing to remove' message."""
        cfg = tmp_path / "nonexistent.json"
        target = GenericMcpTarget(config_path=cfg)
        msg = target.unregister()
        assert "nothing to remove" in msg or "does not exist" in msg

    def test_unregister_when_mneme_not_present(self, tmp_path: Path) -> None:
        """unregister() when mneme is absent returns a 'not found' message."""
        cfg = tmp_path / "mcp.json"
        cfg.write_text(
            json.dumps({"mcpServers": {"other": {"command": "other-mcp"}}}),
            encoding="utf-8",
        )
        target = GenericMcpTarget(config_path=cfg)
        msg = target.unregister()
        assert "not found" in msg or "nothing to remove" in msg

    def test_unregister_no_tmp_leftover(self, tmp_path: Path) -> None:
        """No .mneme-tmp sibling is left after a successful unregister()."""
        cfg = tmp_path / "mcp.json"
        target = GenericMcpTarget(config_path=cfg)
        target.register(tmp_path / "vault")
        target.unregister()
        leftovers = list(tmp_path.rglob("*.mneme-tmp"))
        assert leftovers == [], f"tmp files not cleaned up: {leftovers}"

    def test_round_trip_register_unregister(self, tmp_path: Path) -> None:
        """register() then unregister() leaves an empty mcpServers object."""
        cfg = tmp_path / "mcp.json"
        target = GenericMcpTarget(config_path=cfg)
        target.register(tmp_path / "vault")
        target.unregister()
        data = _load(cfg)
        assert "mneme" not in data.get("mcpServers", {})  # type: ignore[operator]


# ---------------------------------------------------------------------------
# End-to-end CLI tests
# ---------------------------------------------------------------------------


class TestInstallCliMcp:
    @pytest.fixture
    def runner(self) -> CliRunner:
        return CliRunner()

    def test_install_mcp_creates_stanza(
        self, runner: CliRunner, tmp_path: Path
    ) -> None:
        """``mneme install --client mcp --config <path>`` writes the mneme stanza."""
        cfg = tmp_path / "client-mcp.json"
        vault = tmp_path / "vault"

        result = runner.invoke(
            cli,
            [
                "install",
                "--client", "mcp",
                "--config", str(cfg),
                "--vault", str(vault),
                "--skip-python",
                "--skip-node",
            ],
        )

        assert result.exit_code == 0, result.output
        data = _load(cfg)
        mneme = data["mcpServers"]["mneme"]  # type: ignore[index]
        assert isinstance(mneme, dict)
        assert mneme["command"] == "mneme-mcp"
        assert mneme["env"]["MNEME_VAULT"] == str(vault.resolve())  # type: ignore[index]

    def test_install_mcp_without_config_errors(
        self, runner: CliRunner, tmp_path: Path
    ) -> None:
        """``mneme install --client mcp`` without --config must exit non-zero."""
        result = runner.invoke(
            cli,
            [
                "install",
                "--client", "mcp",
                "--vault", str(tmp_path / "vault"),
                "--skip-python",
                "--skip-node",
            ],
        )
        assert result.exit_code != 0
        assert "--config" in result.output or "config" in result.output.lower()

    def test_uninstall_mcp_removes_stanza(
        self, runner: CliRunner, tmp_path: Path
    ) -> None:
        """``mneme uninstall --client mcp --config <path>`` removes the mneme entry."""
        cfg = tmp_path / "client-mcp.json"
        vault = tmp_path / "vault"

        # Install first.
        install_result = runner.invoke(
            cli,
            [
                "install",
                "--client", "mcp",
                "--config", str(cfg),
                "--vault", str(vault),
                "--skip-python",
                "--skip-node",
            ],
        )
        assert install_result.exit_code == 0, install_result.output
        assert "mneme" in _load(cfg).get("mcpServers", {})  # type: ignore[operator]

        # Uninstall.
        uninstall_result = runner.invoke(
            cli,
            [
                "uninstall",
                "--client", "mcp",
                "--config", str(cfg),
            ],
        )
        assert uninstall_result.exit_code == 0, uninstall_result.output
        assert "mneme" not in _load(cfg).get("mcpServers", {})  # type: ignore[operator]

    def test_uninstall_mcp_without_config_errors(
        self, runner: CliRunner, tmp_path: Path
    ) -> None:
        """``mneme uninstall --client mcp`` without --config must exit non-zero."""
        result = runner.invoke(
            cli,
            [
                "uninstall",
                "--client", "mcp",
            ],
        )
        assert result.exit_code != 0
        assert "--config" in result.output or "config" in result.output.lower()

    def test_install_mcp_preserves_other_servers(
        self, runner: CliRunner, tmp_path: Path
    ) -> None:
        """--client mcp install does not disturb pre-existing servers."""
        cfg = tmp_path / "mcp.json"
        cfg.write_text(
            json.dumps({"mcpServers": {"existing": {"command": "existing-mcp"}}}),
            encoding="utf-8",
        )
        vault = tmp_path / "vault"

        result = runner.invoke(
            cli,
            [
                "install",
                "--client", "mcp",
                "--config", str(cfg),
                "--vault", str(vault),
                "--skip-python",
                "--skip-node",
            ],
        )
        assert result.exit_code == 0, result.output
        data = _load(cfg)
        assert data["mcpServers"]["mneme"]["command"] == "mneme-mcp"  # type: ignore[index]
        assert data["mcpServers"]["existing"]["command"] == "existing-mcp"  # type: ignore[index]

    def test_install_mcp_not_included_in_all(
        self, runner: CliRunner, tmp_path: Path
    ) -> None:
        """``--client all`` must NOT require --config (mcp is excluded from all)."""
        settings = tmp_path / ".claude" / "settings.json"
        settings.parent.mkdir(parents=True)
        settings.write_text("{}", encoding="utf-8")

        result = runner.invoke(
            cli,
            [
                "install",
                "--client", "all",
                "--vault", str(tmp_path / "vault"),
                "--settings", str(settings),
                "--backup-dir", str(tmp_path / "bak"),
                "--skip-python",
                "--skip-node",
            ],
        )
        # Must succeed — mcp client is not in the 'all' set.
        assert result.exit_code == 0, result.output
