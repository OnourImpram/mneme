"""Human-approval gate for memory edits (conflict-resolution #4).

An agent *proposes* a memory edit; the proposal starts in ``PENDING`` status.
Edits in *durable* categories (IDENTITY, PREFERENCE, CLINICAL, LEGAL,
FINANCIAL) must receive explicit human approval before they may be applied.
Edits in the EPHEMERAL category (session notes, observations) may be applied
while still PENDING.  A REJECTED proposal can never be applied.

All user-supplied content is passed through :func:`mneme_core.privacy.redact`
before being stored in the proposal.  Proposal IDs are deterministic
(``uuid.uuid5``) so re-proposing identical inputs yields the same ID.

Pure, deterministic, no IO, no network, no clock.
"""

from __future__ import annotations

import dataclasses
import uuid
from dataclasses import dataclass
from enum import Enum

from .privacy import redact
from .scope import DEFAULT_SCOPE, concrete_scope_or_none

# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------

_NAMESPACE = uuid.UUID("6ba7b810-9dad-11d1-80b4-00c04fd430c8")  # NAMESPACE_URL


class ProposalStatus(str, Enum):  # noqa: UP042
    """Lifecycle status of a :class:`MemoryProposal`.

    Inherits ``str`` so instances serialise directly as JSON strings and
    compare equal to their string values without an extra ``.value`` call.
    ``UP042`` suppresses the Ruff suggestion to use ``StrEnum`` (Python 3.11+)
    to stay consistent with the ``str`` + ``Enum`` pattern used elsewhere.
    """

    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"


class EditCategory(str, Enum):  # noqa: UP042
    """Semantic category of the memory edit being proposed.

    * **EPHEMERAL** — low-stakes session/topic/observation; may be applied
      without explicit approval.
    * All other categories are *durable* and require human approval.
    """

    EPHEMERAL = "EPHEMERAL"
    IDENTITY = "IDENTITY"
    PREFERENCE = "PREFERENCE"
    CLINICAL = "CLINICAL"
    LEGAL = "LEGAL"
    FINANCIAL = "FINANCIAL"


#: Categories that require explicit human approval before an edit may be applied.
DURABLE_CATEGORIES: frozenset[EditCategory] = frozenset(
    {
        EditCategory.IDENTITY,
        EditCategory.PREFERENCE,
        EditCategory.CLINICAL,
        EditCategory.LEGAL,
        EditCategory.FINANCIAL,
    }
)

# ---------------------------------------------------------------------------
# Proposal dataclass
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class MemoryProposal:
    """An immutable record of a proposed memory edit.

    Attributes
    ----------
    proposal_id:
        Deterministic ``uuid5`` derived from ``action``, ``target_path``, and
        the *redacted* content.  Identical inputs always produce the same ID.
    action:
        ``"create"`` | ``"update"`` | ``"delete"``.
    target_path:
        Vault-relative path of the note being created/modified/deleted.
    content:
        Proposed note content **after** redaction via
        :func:`mneme_core.privacy.redact`.
    category:
        Semantic category governing approval requirements.
    status:
        Current lifecycle status (PENDING / APPROVED / REJECTED).
    trust:
        Proposer identity string; defaults to ``"agent"``.
    """

    proposal_id: str
    action: str
    target_path: str
    content: str
    category: EditCategory
    status: ProposalStatus
    trust: str
    scope: str = DEFAULT_SCOPE


# ---------------------------------------------------------------------------
# Pure functions
# ---------------------------------------------------------------------------


def requires_human_approval(category: EditCategory) -> bool:
    """Return ``True`` iff *category* is in :data:`DURABLE_CATEGORIES`."""
    return category in DURABLE_CATEGORIES


def propose(
    *,
    action: str,
    target_path: str,
    content: str,
    category: EditCategory,
    trust: str = "agent",
    scope: str = DEFAULT_SCOPE,
) -> MemoryProposal:
    """Create a new :class:`MemoryProposal` in PENDING status.

    Content is redacted via :func:`~mneme_core.privacy.redact` before being
    stored.  The ``proposal_id`` is a deterministic ``uuid5`` derived from
    ``action + NUL + target_path + NUL + redacted_content`` so that identical
    inputs always yield the same proposal ID.
    """
    concrete_scope = concrete_scope_or_none(scope)
    if concrete_scope is None:
        raise ValueError("proposal scope must be a concrete valid identifier")
    redacted = redact(content)
    # category and trust are part of proposal identity: an EPHEMERAL and a
    # CLINICAL proposal for the same path+content must NOT share a proposal_id,
    # else a downstream store keyed on it could alias the durable edit to an
    # already-applied ephemeral one and bypass the human-approval gate.
    seed = f"{action}\x00{target_path}\x00{category.value}\x00{trust}\x00{redacted}"
    if concrete_scope != DEFAULT_SCOPE:
        seed = f"{seed}\x00{concrete_scope}"
    proposal_id = str(uuid.uuid5(_NAMESPACE, seed))
    return MemoryProposal(
        proposal_id=proposal_id,
        action=action,
        target_path=target_path,
        content=redacted,
        category=category,
        status=ProposalStatus.PENDING,
        trust=trust,
        scope=concrete_scope,
    )


def approve(proposal: MemoryProposal) -> MemoryProposal:
    """Return a copy of *proposal* with ``status`` set to APPROVED.

    Idempotent: approving an already-approved proposal returns an equivalent
    object unchanged.
    """
    return dataclasses.replace(proposal, status=ProposalStatus.APPROVED)


def reject(proposal: MemoryProposal) -> MemoryProposal:
    """Return a copy of *proposal* with ``status`` set to REJECTED."""
    return dataclasses.replace(proposal, status=ProposalStatus.REJECTED)


def can_apply(proposal: MemoryProposal) -> bool:
    """Return ``True`` iff the proposal may be applied to the vault.

    Rules (deterministic, pure):

    * A REJECTED proposal can **never** be applied.
    * An APPROVED proposal can always be applied.
    * A PENDING proposal may be applied only when its category is EPHEMERAL
      (i.e. ``requires_human_approval`` is ``False``).
    """
    if proposal.status == ProposalStatus.REJECTED:
        return False
    if proposal.status == ProposalStatus.APPROVED:
        return True
    # PENDING: allowed only for non-durable (ephemeral) categories.
    return not requires_human_approval(proposal.category)
