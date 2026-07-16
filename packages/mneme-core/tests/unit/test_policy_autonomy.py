"""Policy-graduated autonomy: config loading, evaluation, apply, rollback."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from mneme_core.approval import EditCategory, approve, propose
from mneme_core.audit_chain import append_chain_record, verify_chain
from mneme_core.memory_apply import (
    apply_edit,
    drain_proposals,
    list_changes,
    queue_proposal,
    rollback_change,
)
from mneme_core.policy import (
    AutoApproveClass,
    PolicyConfig,
    evaluate,
    load_policy,
)
from mneme_core.vault.config import VaultConfig


@pytest.fixture()
def vault(tmp_path: Path) -> VaultConfig:
    (tmp_path / ".mneme").mkdir(parents=True, exist_ok=True)
    return VaultConfig.from_path(tmp_path)


def _allow_all(vault: VaultConfig) -> None:
    (vault.state_dir / "policy.json").write_text(
        json.dumps({"auto_approve": [c.value for c in AutoApproveClass]}),
        encoding="utf-8",
    )


class TestPolicyLoading:
    def test_absent_file_means_zero_autonomy(self, vault: VaultConfig) -> None:
        policy = load_policy(vault)
        assert policy.allowed_classes == frozenset()

    def test_unknown_class_strings_fail_closed(self, vault: VaultConfig) -> None:
        (vault.state_dir / "policy.json").write_text(
            json.dumps({"auto_approve": ["typo-fix", "rm-rf-everything"]}),
            encoding="utf-8",
        )
        policy = load_policy(vault)
        assert policy.allowed_classes == frozenset({AutoApproveClass.TYPO_FIX})

    def test_malformed_file_means_zero_autonomy(self, vault: VaultConfig) -> None:
        (vault.state_dir / "policy.json").write_text("{oops", encoding="utf-8")
        assert load_policy(vault).allowed_classes == frozenset()


class TestEvaluate:
    def test_durable_category_never_auto(self) -> None:
        policy = PolicyConfig(allowed_classes=frozenset(AutoApproveClass))
        for category in (
            EditCategory.IDENTITY,
            EditCategory.PREFERENCE,
            EditCategory.CLINICAL,
            EditCategory.LEGAL,
            EditCategory.FINANCIAL,
        ):
            decision = evaluate(category, AutoApproveClass.TYPO_FIX, policy)
            assert decision.auto_approved is False

    def test_clinical_lock_blocks_everything(self) -> None:
        policy = PolicyConfig(
            allowed_classes=frozenset(AutoApproveClass), clinical_lock=True
        )
        decision = evaluate(EditCategory.EPHEMERAL, AutoApproveClass.TYPO_FIX, policy)
        assert decision.auto_approved is False
        assert "clinical_lock" in decision.reason

    def test_allowed_class_auto_approves_ephemeral(self) -> None:
        policy = PolicyConfig(allowed_classes=frozenset({AutoApproveClass.DEDUP_MERGE}))
        ok = evaluate(EditCategory.EPHEMERAL, AutoApproveClass.DEDUP_MERGE, policy)
        no = evaluate(EditCategory.EPHEMERAL, AutoApproveClass.TYPO_FIX, policy)
        assert ok.auto_approved is True
        assert no.auto_approved is False

    def test_no_class_declared_refused(self) -> None:
        policy = PolicyConfig(allowed_classes=frozenset(AutoApproveClass))
        assert evaluate(EditCategory.EPHEMERAL, None, policy).auto_approved is False


class TestApplyEdit:
    def test_autonomous_apply_within_policy(self, vault: VaultConfig) -> None:
        _allow_all(vault)
        proposal = propose(
            action="create",
            target_path="notes/fact.md",
            content="A fact.",
            category=EditCategory.EPHEMERAL,
        )
        result = apply_edit(vault, proposal, AutoApproveClass.TYPO_FIX)
        assert result.applied is True
        written = (vault.root / "notes/fact.md").read_text(encoding="utf-8")
        assert 'scope: "default"' in written
        assert written.endswith("A fact.")

    def test_refused_without_policy_file(self, vault: VaultConfig) -> None:
        proposal = propose(
            action="create",
            target_path="notes/fact.md",
            content="A fact.",
            category=EditCategory.EPHEMERAL,
        )
        result = apply_edit(vault, proposal, AutoApproveClass.TYPO_FIX)
        assert result.applied is False
        assert not (vault.root / "notes/fact.md").exists()

    def test_durable_pending_refused_even_with_policy(self, vault: VaultConfig) -> None:
        _allow_all(vault)
        proposal = propose(
            action="create",
            target_path="identity/me.md",
            content="New identity.",
            category=EditCategory.IDENTITY,
        )
        result = apply_edit(vault, proposal, AutoApproveClass.TYPO_FIX)
        assert result.applied is False

    def test_durable_approved_applies(self, vault: VaultConfig) -> None:
        proposal = approve(
            propose(
                action="create",
                target_path="identity/me.md",
                content="New identity.",
                category=EditCategory.IDENTITY,
            )
        )
        result = apply_edit(vault, proposal, None)
        assert result.applied is True

    def test_path_escape_refused(self, vault: VaultConfig) -> None:
        _allow_all(vault)
        proposal = propose(
            action="create",
            target_path="../outside.md",
            content="escape",
            category=EditCategory.EPHEMERAL,
        )
        result = apply_edit(vault, proposal, AutoApproveClass.TYPO_FIX)
        assert result.applied is False
        assert "escape" in result.reason

    def test_audit_chain_grows_and_verifies(self, vault: VaultConfig) -> None:
        _allow_all(vault)
        for i in range(3):
            proposal = propose(
                action="create",
                target_path=f"notes/n{i}.md",
                content=f"note {i}",
                category=EditCategory.EPHEMERAL,
            )
            assert apply_edit(vault, proposal, AutoApproveClass.DEDUP_MERGE).applied
        day = datetime.now(UTC).strftime("%Y-%m-%d")
        report = verify_chain(vault.state_dir, day)
        assert report.valid is True
        assert report.records == 3

    def test_tampered_chain_detected(self, vault: VaultConfig) -> None:
        append_chain_record(vault.state_dir, {"kind": "memory_edit", "relative_path": "a.md"})
        append_chain_record(vault.state_dir, {"kind": "memory_edit", "relative_path": "b.md"})
        day = datetime.now(UTC).strftime("%Y-%m-%d")
        chain_file = vault.state_dir / "audit" / f"{day}.jsonl"
        lines = chain_file.read_text(encoding="utf-8").splitlines()
        lines[0] = lines[0].replace("a.md", "tampered.md")
        chain_file.write_text("\n".join(lines) + "\n", encoding="utf-8")
        report = verify_chain(vault.state_dir, day)
        assert report.valid is False
        assert report.first_break_line == 1


    def test_create_never_overwrites_existing_target(self, vault: VaultConfig) -> None:
        _allow_all(vault)
        target = vault.root / "notes/existing.md"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("original", encoding="utf-8")
        proposal = propose(
            action="create",
            target_path="notes/existing.md",
            content="replacement",
            category=EditCategory.EPHEMERAL,
        )
        result = apply_edit(vault, proposal, AutoApproveClass.TYPO_FIX)
        assert result.applied is False
        assert "already exists" in result.reason
        assert target.read_text(encoding="utf-8") == "original"

    def test_update_never_creates_missing_target(self, vault: VaultConfig) -> None:
        _allow_all(vault)
        proposal = propose(
            action="update",
            target_path="notes/missing.md",
            content="replacement",
            category=EditCategory.EPHEMERAL,
        )
        result = apply_edit(vault, proposal, AutoApproveClass.TYPO_FIX)
        assert result.applied is False
        assert "does not exist" in result.reason
        assert not (vault.root / "notes/missing.md").exists()

    def test_cross_scope_update_is_refused(self, vault: VaultConfig) -> None:
        _allow_all(vault)
        target = vault.root / "notes/clinical.md"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text('---\nscope: "clinical"\n---\nsecret', encoding="utf-8")
        proposal = propose(
            action="update",
            target_path="notes/clinical.md",
            content="replacement",
            category=EditCategory.EPHEMERAL,
            scope="default",
        )
        result = apply_edit(vault, proposal, AutoApproveClass.TYPO_FIX)
        assert result.applied is False
        assert "outside" in result.reason
        assert target.read_text(encoding="utf-8").endswith("secret")

    def test_non_default_create_is_stamped_and_journalled(self, vault: VaultConfig) -> None:
        _allow_all(vault)
        proposal = propose(
            action="create",
            target_path="notes/clinical.md",
            content="clinical memory",
            category=EditCategory.EPHEMERAL,
            scope="clinical",
        )
        result = apply_edit(vault, proposal, AutoApproveClass.TYPO_FIX)
        assert result.applied and result.change_id
        written = (vault.root / "notes/clinical.md").read_text(encoding="utf-8")
        assert 'scope: "clinical"' in written
        journal = json.loads(
            (vault.state_dir / "rollback" / f"{result.change_id}.json").read_text(
                encoding="utf-8"
            )
        )
        assert journal["scope"] == "clinical"

    def test_proposal_scope_participates_in_non_default_identity(self) -> None:
        default = propose(
            action="create",
            target_path="notes/x.md",
            content="same",
            category=EditCategory.EPHEMERAL,
        )
        alpha = propose(
            action="create",
            target_path="notes/x.md",
            content="same",
            category=EditCategory.EPHEMERAL,
            scope="alpha",
        )
        beta = propose(
            action="create",
            target_path="notes/x.md",
            content="same",
            category=EditCategory.EPHEMERAL,
            scope="beta",
        )
        assert len({default.proposal_id, alpha.proposal_id, beta.proposal_id}) == 3
        with pytest.raises(ValueError):
            propose(
                action="create",
                target_path="notes/x.md",
                content="same",
                category=EditCategory.EPHEMERAL,
                scope="*",
            )


class TestRollback:
    def _apply(self, vault: VaultConfig, path: str, content: str, action: str = "create"):
        _allow_all(vault)
        proposal = propose(
            action=action,
            target_path=path,
            content=content,
            category=EditCategory.EPHEMERAL,
        )
        return apply_edit(vault, proposal, AutoApproveClass.TYPO_FIX)

    def test_rollback_create_removes_file(self, vault: VaultConfig) -> None:
        result = self._apply(vault, "notes/new.md", "fresh")
        assert result.applied and result.change_id
        rb = rollback_change(vault, result.change_id)
        assert rb.applied is True
        assert not (vault.root / "notes/new.md").exists()

    def test_rollback_update_restores_prior(self, vault: VaultConfig) -> None:
        target = vault.root / "notes/exist.md"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("original", encoding="utf-8")
        result = self._apply(vault, "notes/exist.md", "replaced", action="update")
        assert result.applied and result.change_id
        assert target.read_text(encoding="utf-8").endswith("replaced")
        rb = rollback_change(vault, result.change_id)
        assert rb.applied is True
        assert target.read_text(encoding="utf-8") == "original"

    def test_double_rollback_refused(self, vault: VaultConfig) -> None:
        result = self._apply(vault, "notes/x.md", "x")
        assert result.change_id
        assert rollback_change(vault, result.change_id).applied is True
        again = rollback_change(vault, result.change_id)
        assert again.applied is False

    def test_rollback_refuses_target_reclassified_to_another_scope(
        self, vault: VaultConfig
    ) -> None:
        result = self._apply(vault, "notes/moved.md", "created")
        assert result.applied and result.change_id
        target = vault.root / "notes/moved.md"
        target.write_text('---\nscope: "clinical"\n---\nforeign', encoding="utf-8")
        rollback = rollback_change(vault, result.change_id)
        assert rollback.applied is False
        assert "outside" in rollback.reason
        assert target.read_text(encoding="utf-8").endswith("foreign")

    def test_unknown_change_id_refused(self, vault: VaultConfig) -> None:
        assert rollback_change(vault, "nope").applied is False

    def test_list_changes_reports_status(self, vault: VaultConfig) -> None:
        result = self._apply(vault, "notes/list.md", "content")
        assert result.change_id
        changes = list_changes(vault)
        assert changes[0]["change_id"] == result.change_id
        assert changes[0]["status"] == "applied"
        assert "prior_content" not in changes[0]


class TestQueueAndDrain:
    def test_drain_applies_allowed_and_keeps_refused(self, vault: VaultConfig) -> None:
        (vault.state_dir / "policy.json").write_text(
            json.dumps({"auto_approve": ["typo-fix"]}), encoding="utf-8"
        )
        ok = propose(
            action="create",
            target_path="notes/ok.md",
            content="ok",
            category=EditCategory.EPHEMERAL,
        )
        bad_class = propose(
            action="create",
            target_path="notes/bad.md",
            content="bad",
            category=EditCategory.EPHEMERAL,
        )
        queue_proposal(vault, ok, AutoApproveClass.TYPO_FIX)
        queue_proposal(vault, bad_class, AutoApproveClass.STALE_ARCHIVE)
        report = drain_proposals(vault)
        assert report.applied == 1
        assert report.refused == 1
        assert (vault.root / "notes/ok.md").is_file()
        assert not (vault.root / "notes/bad.md").exists()
        processed_dir = vault.state_dir / "proposals" / "processed"
        refused_files = list(processed_dir.glob("*.refused.jsonl"))
        assert len(refused_files) == 1
        assert len(list(processed_dir.glob("*.processed.jsonl"))) == 1
        assert not (vault.state_dir / "proposals" / "pending.jsonl").exists()

    def test_drain_preserves_scope_and_legacy_missing_scope_is_default(
        self, vault: VaultConfig
    ) -> None:
        (vault.state_dir / "policy.json").write_text(
            json.dumps({"auto_approve": ["typo-fix"]}), encoding="utf-8"
        )
        scoped = propose(
            action="create",
            target_path="notes/scoped.md",
            content="scoped",
            category=EditCategory.EPHEMERAL,
            scope="clinical",
        )
        queue_proposal(vault, scoped, AutoApproveClass.TYPO_FIX)
        report = drain_proposals(vault)
        assert report.applied == 1
        assert 'scope: "clinical"' in (
            vault.root / "notes/scoped.md"
        ).read_text(encoding="utf-8")

        queue = vault.state_dir / "proposals" / "pending.jsonl"
        legacy = propose(
            action="create",
            target_path="notes/legacy.md",
            content="legacy",
            category=EditCategory.EPHEMERAL,
        )
        queue.parent.mkdir(parents=True, exist_ok=True)
        record = {
            "proposal_id": legacy.proposal_id,
            "action": legacy.action,
            "target_path": legacy.target_path,
            "content": legacy.content,
            "category": legacy.category.value,
            "status": legacy.status.value,
            "trust": legacy.trust,
            "edit_class": AutoApproveClass.TYPO_FIX.value,
        }
        queue.write_text(json.dumps(record) + "\n", encoding="utf-8")
        legacy_report = drain_proposals(vault)
        assert legacy_report.applied == 1
        assert 'scope: "default"' in (
            vault.root / "notes/legacy.md"
        ).read_text(encoding="utf-8")

    def test_wildcard_scope_queue_record_is_malformed(self, vault: VaultConfig) -> None:
        queue = vault.state_dir / "proposals" / "pending.jsonl"
        queue.parent.mkdir(parents=True, exist_ok=True)
        queue.write_text(
            json.dumps(
                {
                    "proposal_id": "x",
                    "action": "create",
                    "target_path": "notes/x.md",
                    "content": "x",
                    "category": "EPHEMERAL",
                    "scope": "*",
                    "edit_class": "typo-fix",
                }
            )
            + "\n",
            encoding="utf-8",
        )
        report = drain_proposals(vault)
        assert report.malformed == 1
        assert report.applied == 0
        assert not (vault.root / "notes/x.md").exists()

    def test_drain_empty_queue_noop(self, vault: VaultConfig) -> None:
        report = drain_proposals(vault)
        assert report.applied == 0 and report.refused == 0

    def test_malformed_queue_line_counted(self, vault: VaultConfig) -> None:
        queue = vault.state_dir / "proposals"
        queue.mkdir(parents=True, exist_ok=True)
        (queue / "pending.jsonl").write_text("{broken\n", encoding="utf-8")
        report = drain_proposals(vault)
        assert report.malformed == 1


class TestPolicyScaffold:
    """policy init template + validate report (WS1b autonomy UX)."""

    def test_default_payload_grants_zero_autonomy(self, tmp_path: Path) -> None:
        import json as _json

        from mneme_core.policy import default_policy_payload, load_policy
        from mneme_core.vault.config import VaultConfig

        vault = VaultConfig.from_path(tmp_path)
        vault.state_dir.mkdir(parents=True, exist_ok=True)
        (vault.state_dir / "policy.json").write_text(
            _json.dumps(default_policy_payload()), encoding="utf-8"
        )
        policy = load_policy(vault)
        assert policy.allowed_classes == frozenset()
        assert policy.clinical_lock is False

    def test_default_payload_documents_every_class(self) -> None:
        from mneme_core.policy import AutoApproveClass, default_policy_payload

        docs = str(default_policy_payload()["_docs"])
        for cls in AutoApproveClass:
            assert cls.value in docs

    def test_inspect_absent_file(self, tmp_path: Path) -> None:
        from mneme_core.policy import inspect_policy
        from mneme_core.vault.config import VaultConfig

        vault = VaultConfig.from_path(tmp_path)
        report = inspect_policy(vault)
        assert report["exists"] is False
        assert report["valid"] is True

    def test_inspect_surfaces_unknown_classes(self, tmp_path: Path) -> None:
        import json as _json

        from mneme_core.policy import inspect_policy
        from mneme_core.vault.config import VaultConfig

        vault = VaultConfig.from_path(tmp_path)
        vault.state_dir.mkdir(parents=True, exist_ok=True)
        (vault.state_dir / "policy.json").write_text(
            _json.dumps({"auto_approve": ["typo-fix", "typofix"]}),
            encoding="utf-8",
        )
        report = inspect_policy(vault)
        assert report["valid"] is True
        assert report["auto_approve"] == ["typo-fix"]
        assert report["unknown_classes"] == ["typofix"]

    def test_inspect_invalid_json(self, tmp_path: Path) -> None:
        from mneme_core.policy import inspect_policy
        from mneme_core.vault.config import VaultConfig

        vault = VaultConfig.from_path(tmp_path)
        vault.state_dir.mkdir(parents=True, exist_ok=True)
        (vault.state_dir / "policy.json").write_text("{nope", encoding="utf-8")
        report = inspect_policy(vault)
        assert report["valid"] is False
