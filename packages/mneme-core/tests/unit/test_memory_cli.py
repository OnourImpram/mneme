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
