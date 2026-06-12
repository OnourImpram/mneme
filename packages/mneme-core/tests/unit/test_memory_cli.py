"""CLI surface for the ``memory`` group (policy/changes/rollback/drain)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from mneme_core.approval import EditCategory, propose
from mneme_core.cli import cli
from mneme_core.memory_apply import apply_edit, queue_proposal
from mneme_core.policy import AutoApproveClass
from mneme_core.vault.config import VaultConfig


@pytest.fixture()
def vault(tmp_path: Path) -> VaultConfig:
    v = VaultConfig.from_path(tmp_path)
    v.state_dir.mkdir(parents=True, exist_ok=True)
    (v.state_dir / "policy.json").write_text(
        json.dumps({"auto_approve": ["typo-fix"]}), encoding="utf-8"
    )
    return v


class TestMemoryCli:
    def test_policy_show(self, vault: VaultConfig) -> None:
        runner = CliRunner()
        result = runner.invoke(cli, ["memory", "policy", "--vault", str(vault.root)])
        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        assert data["auto_approve"] == ["typo-fix"]
        assert data["clinical_lock"] is False

    def test_drain_then_changes_then_rollback(self, vault: VaultConfig) -> None:
        proposal = propose(
            action="create",
            target_path="notes/cli.md",
            content="via cli",
            category=EditCategory.EPHEMERAL,
        )
        queue_proposal(vault, proposal, AutoApproveClass.TYPO_FIX)
        runner = CliRunner()

        drained = runner.invoke(cli, ["memory", "drain", "--vault", str(vault.root)])
        assert drained.exit_code == 0, drained.output
        assert json.loads(drained.output)["applied"] == 1
        assert (vault.root / "notes/cli.md").is_file()

        changes = runner.invoke(cli, ["memory", "changes", "--vault", str(vault.root)])
        data = json.loads(changes.output)
        assert data["count"] == 1
        change_id = data["changes"][0]["change_id"]

        rolled = runner.invoke(
            cli, ["memory", "rollback", change_id, "--vault", str(vault.root)]
        )
        assert rolled.exit_code == 0, rolled.output
        assert not (vault.root / "notes/cli.md").exists()

    def test_rollback_unknown_id_exits_nonzero(self, vault: VaultConfig) -> None:
        runner = CliRunner()
        result = runner.invoke(
            cli, ["memory", "rollback", "ghost", "--vault", str(vault.root)]
        )
        assert result.exit_code == 1

    def test_apply_edit_visible_in_changes(self, vault: VaultConfig) -> None:
        proposal = propose(
            action="create",
            target_path="notes/direct.md",
            content="direct",
            category=EditCategory.EPHEMERAL,
        )
        assert apply_edit(vault, proposal, AutoApproveClass.TYPO_FIX).applied
        runner = CliRunner()
        changes = runner.invoke(cli, ["memory", "changes", "--vault", str(vault.root)])
        assert json.loads(changes.output)["count"] == 1


class TestPolicyInitValidate:
    """memory policy init/validate subcommands (WS1b)."""

    def test_init_creates_then_never_overwrites(self, tmp_path: Path) -> None:
        runner = CliRunner()
        root = tmp_path / "fresh"
        root.mkdir()
        first = runner.invoke(
            cli, ["memory", "policy", "init", "--vault", str(root)]
        )
        assert first.exit_code == 0, first.output
        assert json.loads(first.output)["created"] is True
        path = root / ".mneme" / "policy.json"
        original = path.read_bytes()
        second = runner.invoke(
            cli, ["memory", "policy", "init", "--vault", str(root)]
        )
        assert second.exit_code == 0, second.output
        assert json.loads(second.output)["created"] is False
        assert path.read_bytes() == original

    def test_policy_show_still_works_as_bare_group(self, vault: VaultConfig) -> None:
        runner = CliRunner()
        result = runner.invoke(cli, ["memory", "policy", "--vault", str(vault.root)])
        assert result.exit_code == 0, result.output
        assert json.loads(result.output)["auto_approve"] == ["typo-fix"]

    def test_validate_ok_exit_zero(self, vault: VaultConfig) -> None:
        runner = CliRunner()
        result = runner.invoke(
            cli, ["memory", "policy", "validate", "--vault", str(vault.root)]
        )
        assert result.exit_code == 0, result.output
        report = json.loads(result.output)
        assert report["valid"] is True
        assert report["unknown_classes"] == []

    def test_validate_unknown_class_exit_one(self, tmp_path: Path) -> None:
        runner = CliRunner()
        root = tmp_path / "typo"
        (root / ".mneme").mkdir(parents=True)
        (root / ".mneme" / "policy.json").write_text(
            json.dumps({"auto_approve": ["dedupmerge"]}), encoding="utf-8"
        )
        result = runner.invoke(
            cli, ["memory", "policy", "validate", "--vault", str(root)]
        )
        assert result.exit_code == 1
        assert json.loads(result.output)["unknown_classes"] == ["dedupmerge"]
