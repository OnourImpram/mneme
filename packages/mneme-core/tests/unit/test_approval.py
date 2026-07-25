"""Tests for :mod:`mneme_core.approval` — human-approval gate."""

from __future__ import annotations

import pytest

from mneme_core.approval import (
    DURABLE_CATEGORIES,
    EditCategory,
    MemoryProposal,
    ProposalStatus,
    approve,
    can_apply,
    propose,
    reject,
    requires_human_approval,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _ephemeral(**kwargs: object) -> MemoryProposal:
    return propose(
        action=str(kwargs.get("action", "create")),
        target_path=str(kwargs.get("target_path", "notes/tmp.md")),
        content=str(kwargs.get("content", "hello")),
        category=EditCategory.EPHEMERAL,
    )


def _durable(category: EditCategory = EditCategory.CLINICAL, **kwargs: object) -> MemoryProposal:
    return propose(
        action=str(kwargs.get("action", "update")),
        target_path=str(kwargs.get("target_path", "core/profile.md")),
        content=str(kwargs.get("content", "some content")),
        category=category,
    )


class TestProposalIdIdentity:
    def test_id_differs_by_category(self) -> None:
        # A3: category is part of identity — an EPHEMERAL and a CLINICAL proposal
        # for the same action/path/content must NOT collide (else a durable edit
        # could be aliased to an already-applied ephemeral one, bypassing approval).
        eph = propose(
            action="update",
            target_path="core/x.md",
            content="same",
            category=EditCategory.EPHEMERAL,
        )
        clin = propose(
            action="update",
            target_path="core/x.md",
            content="same",
            category=EditCategory.CLINICAL,
        )
        assert eph.proposal_id != clin.proposal_id

    def test_id_differs_by_trust(self) -> None:
        a = propose(
            action="update",
            target_path="core/x.md",
            content="same",
            category=EditCategory.CLINICAL,
            trust="agent",
        )
        b = propose(
            action="update",
            target_path="core/x.md",
            content="same",
            category=EditCategory.CLINICAL,
            trust="human",
        )
        assert a.proposal_id != b.proposal_id

    def test_id_deterministic_for_identical_inputs(self) -> None:
        a = propose(
            action="update",
            target_path="core/x.md",
            content="same",
            category=EditCategory.CLINICAL,
            trust="agent",
        )
        b = propose(
            action="update",
            target_path="core/x.md",
            content="same",
            category=EditCategory.CLINICAL,
            trust="agent",
        )
        assert a.proposal_id == b.proposal_id

    def test_non_default_scopes_do_not_alias(self) -> None:
        clinical = propose(
            action="update",
            target_path="core/x.md",
            content="same",
            category=EditCategory.EPHEMERAL,
            scope="clinical",
        )
        research = propose(
            action="update",
            target_path="core/x.md",
            content="same",
            category=EditCategory.EPHEMERAL,
            scope="research",
        )
        assert clinical.proposal_id != research.proposal_id

    def test_wildcard_scope_is_never_a_durable_proposal(self) -> None:
        with pytest.raises(ValueError, match="concrete"):
            propose(
                action="create",
                target_path="notes/x.md",
                content="x",
                category=EditCategory.EPHEMERAL,
                scope="*",
            )


# ---------------------------------------------------------------------------
# requires_human_approval
# ---------------------------------------------------------------------------


class TestRequiresHumanApproval:
    def test_ephemeral_does_not_require_approval(self) -> None:
        assert requires_human_approval(EditCategory.EPHEMERAL) is False

    @pytest.mark.parametrize(
        "category",
        [
            EditCategory.IDENTITY,
            EditCategory.PREFERENCE,
            EditCategory.CLINICAL,
            EditCategory.LEGAL,
            EditCategory.FINANCIAL,
        ],
    )
    def test_all_durable_categories_require_approval(self, category: EditCategory) -> None:
        assert requires_human_approval(category) is True

    def test_durable_categories_set_matches_enum(self) -> None:
        non_ephemeral = {c for c in EditCategory if c != EditCategory.EPHEMERAL}
        assert non_ephemeral == DURABLE_CATEGORIES


# ---------------------------------------------------------------------------
# can_apply — EPHEMERAL / auto-apply
# ---------------------------------------------------------------------------


class TestCanApplyEphemeral:
    def test_pending_ephemeral_can_apply(self) -> None:
        p = _ephemeral()
        assert p.status == ProposalStatus.PENDING
        assert can_apply(p) is True

    def test_approved_ephemeral_can_apply(self) -> None:
        p = approve(_ephemeral())
        assert can_apply(p) is True

    def test_rejected_ephemeral_cannot_apply(self) -> None:
        p = reject(_ephemeral())
        assert can_apply(p) is False


# ---------------------------------------------------------------------------
# can_apply — DURABLE categories must not auto-apply
# ---------------------------------------------------------------------------


class TestCanApplyDurable:
    def test_pending_clinical_cannot_apply(self) -> None:
        p = _durable(EditCategory.CLINICAL)
        assert p.status == ProposalStatus.PENDING
        assert can_apply(p) is False

    def test_pending_legal_cannot_apply(self) -> None:
        p = _durable(EditCategory.LEGAL)
        assert can_apply(p) is False

    def test_pending_identity_cannot_apply(self) -> None:
        p = _durable(EditCategory.IDENTITY)
        assert can_apply(p) is False

    def test_pending_preference_cannot_apply(self) -> None:
        p = _durable(EditCategory.PREFERENCE)
        assert can_apply(p) is False

    def test_pending_financial_cannot_apply(self) -> None:
        p = _durable(EditCategory.FINANCIAL)
        assert can_apply(p) is False

    def test_approved_clinical_can_apply(self) -> None:
        p = approve(_durable(EditCategory.CLINICAL))
        assert can_apply(p) is True

    def test_rejected_clinical_cannot_apply(self) -> None:
        p = reject(_durable(EditCategory.CLINICAL))
        assert can_apply(p) is False


# ---------------------------------------------------------------------------
# approve / reject transitions
# ---------------------------------------------------------------------------


class TestApproveReject:
    def test_approve_sets_status(self) -> None:
        p = _durable()
        approved = approve(p)
        assert approved.status == ProposalStatus.APPROVED

    def test_approve_is_idempotent(self) -> None:
        p = approve(_durable())
        assert approve(p).status == ProposalStatus.APPROVED

    def test_reject_sets_status(self) -> None:
        p = _durable()
        rejected = reject(p)
        assert rejected.status == ProposalStatus.REJECTED

    def test_reject_ephemeral_blocks_apply(self) -> None:
        p = reject(_ephemeral())
        assert can_apply(p) is False

    def test_original_proposal_unchanged_after_approve(self) -> None:
        p = _durable()
        _ = approve(p)
        assert p.status == ProposalStatus.PENDING  # immutable original

    def test_original_proposal_unchanged_after_reject(self) -> None:
        p = _ephemeral()
        _ = reject(p)
        assert p.status == ProposalStatus.PENDING


# ---------------------------------------------------------------------------
# Content redaction
# ---------------------------------------------------------------------------


class TestContentRedaction:
    def test_private_tag_is_redacted(self) -> None:
        p = propose(
            action="create",
            target_path="notes/secret.md",
            content="name: <private>John Doe</private>",
            category=EditCategory.IDENTITY,
        )
        assert "[REDACTED]" in p.content
        assert "John Doe" not in p.content

    def test_plain_content_passes_through(self) -> None:
        p = propose(
            action="create",
            target_path="notes/plain.md",
            content="just a plain note",
            category=EditCategory.EPHEMERAL,
        )
        assert p.content == "just a plain note"


# ---------------------------------------------------------------------------
# Deterministic proposal_id
# ---------------------------------------------------------------------------


class TestDeterministicProposalId:
    def test_identical_inputs_yield_same_id(self) -> None:
        p1 = propose(
            action="update",
            target_path="core/profile.md",
            content="foo bar",
            category=EditCategory.PREFERENCE,
        )
        p2 = propose(
            action="update",
            target_path="core/profile.md",
            content="foo bar",
            category=EditCategory.PREFERENCE,
        )
        assert p1.proposal_id == p2.proposal_id

    def test_different_content_yields_different_id(self) -> None:
        p1 = propose(
            action="update",
            target_path="core/profile.md",
            content="foo",
            category=EditCategory.PREFERENCE,
        )
        p2 = propose(
            action="update",
            target_path="core/profile.md",
            content="bar",
            category=EditCategory.PREFERENCE,
        )
        assert p1.proposal_id != p2.proposal_id

    def test_different_action_yields_different_id(self) -> None:
        p1 = propose(
            action="create",
            target_path="notes/x.md",
            content="content",
            category=EditCategory.EPHEMERAL,
        )
        p2 = propose(
            action="delete",
            target_path="notes/x.md",
            content="content",
            category=EditCategory.EPHEMERAL,
        )
        assert p1.proposal_id != p2.proposal_id


# ---------------------------------------------------------------------------
# delete action
# ---------------------------------------------------------------------------


class TestDeleteAction:
    def test_delete_proposal_created(self) -> None:
        p = propose(
            action="delete",
            target_path="notes/old.md",
            content="",
            category=EditCategory.EPHEMERAL,
        )
        assert p.action == "delete"
        assert p.status == ProposalStatus.PENDING

    def test_pending_delete_ephemeral_can_apply(self) -> None:
        p = propose(
            action="delete",
            target_path="notes/old.md",
            content="",
            category=EditCategory.EPHEMERAL,
        )
        assert can_apply(p) is True

    def test_pending_delete_durable_cannot_apply(self) -> None:
        p = propose(
            action="delete",
            target_path="core/identity.md",
            content="",
            category=EditCategory.IDENTITY,
        )
        assert can_apply(p) is False


# ---------------------------------------------------------------------------
# trust field
# ---------------------------------------------------------------------------


class TestTrustField:
    def test_default_trust_is_agent(self) -> None:
        p = _ephemeral()
        assert p.trust == "agent"

    def test_custom_trust(self) -> None:
        p = propose(
            action="create",
            target_path="notes/x.md",
            content="hi",
            category=EditCategory.EPHEMERAL,
            trust="human",
        )
        assert p.trust == "human"
