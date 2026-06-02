"""Capability firewall for mneme-core (plan invariant 4).

Retrieved or tainted content can **never** grant a mutating or
outward-reaching capability; at most ``READ`` is permitted.  This module
encodes that invariant as a pure, deterministic policy engine — no LLM, no
network, no IO, no clock.

The firewall is intentionally deny-by-default: every ``CapabilityDecision``
carries a human-readable ``reason`` so callers can surface audit trails.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class Capability(str, Enum):  # noqa: UP042
    """Enumeration of capabilities a caller may request.

    Inherits ``str`` so instances serialise directly as JSON strings and
    compare equal to their string values without an extra ``.value`` call.
    The ``UP042`` noqa suppresses the Ruff suggestion to use ``StrEnum``
    (Python 3.11+) which would break the ``str`` + ``Enum`` pattern already
    used elsewhere in the codebase.
    """

    READ = "READ"
    WRITE = "WRITE"
    NETWORK = "NETWORK"
    TOOL_EXEC = "TOOL_EXEC"
    ARTIFACT_UPLOAD = "ARTIFACT_UPLOAD"
    DELETE = "DELETE"


# Mutating / outward-reaching capabilities that tainted or non-user content
# may NEVER be granted.
MUTATING_CAPABILITIES: frozenset[Capability] = frozenset(
    {
        Capability.WRITE,
        Capability.NETWORK,
        Capability.TOOL_EXEC,
        Capability.ARTIFACT_UPLOAD,
        Capability.DELETE,
    }
)


@dataclass(frozen=True)
class CapabilityRequest:
    """An immutable description of a capability being requested."""

    capability: Capability
    source_trust: str  # provenance trust of the content requesting the action
    tainted: bool  # caller-computed (e.g. taint label != USER)


@dataclass(frozen=True)
class CapabilityDecision:
    """An immutable capability decision with a human-readable audit reason."""

    allowed: bool
    reason: str


@dataclass(frozen=True)
class CapabilityPolicy:
    """Operator policy: the set of capabilities a verified user may exercise."""

    user_capabilities: frozenset[Capability]


# Default policy: operator may READ, WRITE, and DELETE.
# NETWORK / TOOL_EXEC / ARTIFACT_UPLOAD are opt-in, never default.
DEFAULT_POLICY: CapabilityPolicy = CapabilityPolicy(
    user_capabilities=frozenset({Capability.READ, Capability.WRITE, Capability.DELETE})
)

_TAINTED_DENY_REASON = (
    "retrieved/untrusted content cannot mutate or reach out "
    "(plan invariant 4: data-not-instruction)"
)


def evaluate(
    req: CapabilityRequest,
    policy: CapabilityPolicy = DEFAULT_POLICY,
) -> CapabilityDecision:
    """Evaluate a capability request against *policy*.

    Rules (deny-by-default, deterministic), applied in order:

    1. **Tainted or non-user source** (``req.tainted`` is ``True`` *or*
       ``req.source_trust != "user"``):
       allow **only** ``READ``; deny everything else with a reason naming the
       invariant (``"retrieved/untrusted content cannot mutate or reach out"``).

    2. **Untainted user content** (``req.tainted`` is ``False`` *and*
       ``req.source_trust == "user"``):
       allow iff ``req.capability`` is in ``policy.user_capabilities``, else
       deny with ``"capability not granted to user by policy"``.

    Every returned ``CapabilityDecision`` carries a non-empty ``reason``.
    """
    # Normalise the trust string (mirrors taint.taint_for_trust) so the two
    # modules agree on what "user" means; otherwise "User"/"USER" would be USER
    # to the taint layer but non-user here.
    is_user_content = (not req.tainted) and (req.source_trust.strip().lower() == "user")

    if not is_user_content:
        # Rule 1: tainted or non-user content may exercise only NON-mutating
        # capabilities (anything outside MUTATING_CAPABILITIES, currently READ).
        # Wiring the constant makes the firewall machine-checked: a future
        # Capability member added to MUTATING_CAPABILITIES is denied automatically.
        if req.capability not in MUTATING_CAPABILITIES:
            return CapabilityDecision(
                allowed=True,
                reason=f"{req.capability.value} is non-mutating; permitted for untrusted content",
            )
        return CapabilityDecision(allowed=False, reason=_TAINTED_DENY_REASON)

    # Rule 2: untainted user content — check policy grant.
    if req.capability in policy.user_capabilities:
        return CapabilityDecision(
            allowed=True,
            reason=f"{req.capability.value} is granted to user by policy",
        )
    return CapabilityDecision(
        allowed=False,
        reason="capability not granted to user by policy",
    )
