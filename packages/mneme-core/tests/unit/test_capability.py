"""Unit tests for mneme_core.capability — capability firewall."""

from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from mneme_core.capability import (
    DEFAULT_POLICY,
    MUTATING_CAPABILITIES,
    Capability,
    CapabilityDecision,
    CapabilityPolicy,
    CapabilityRequest,
    evaluate,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _req(
    cap: Capability,
    *,
    trust: str = "external",
    tainted: bool = True,
) -> CapabilityRequest:
    return CapabilityRequest(capability=cap, source_trust=trust, tainted=tainted)


# ---------------------------------------------------------------------------
# MUTATING_CAPABILITIES constant
# ---------------------------------------------------------------------------


class TestMutatingCapabilities:
    def test_write_is_mutating(self) -> None:
        assert Capability.WRITE in MUTATING_CAPABILITIES

    def test_network_is_mutating(self) -> None:
        assert Capability.NETWORK in MUTATING_CAPABILITIES

    def test_tool_exec_is_mutating(self) -> None:
        assert Capability.TOOL_EXEC in MUTATING_CAPABILITIES

    def test_artifact_upload_is_mutating(self) -> None:
        assert Capability.ARTIFACT_UPLOAD in MUTATING_CAPABILITIES

    def test_delete_is_mutating(self) -> None:
        assert Capability.DELETE in MUTATING_CAPABILITIES

    def test_read_is_not_mutating(self) -> None:
        assert Capability.READ not in MUTATING_CAPABILITIES


# ---------------------------------------------------------------------------
# Frozen dataclasses
# ---------------------------------------------------------------------------


class TestFrozenDataclasses:
    def test_capability_request_frozen(self) -> None:
        req = CapabilityRequest(capability=Capability.READ, source_trust="user", tainted=False)
        with pytest.raises(FrozenInstanceError):
            req.capability = Capability.WRITE  # type: ignore[misc]

    def test_capability_decision_frozen(self) -> None:
        dec = CapabilityDecision(allowed=True, reason="ok")
        with pytest.raises(FrozenInstanceError):
            dec.allowed = False  # type: ignore[misc]

    def test_capability_policy_frozen(self) -> None:
        pol = CapabilityPolicy(user_capabilities=frozenset({Capability.READ}))
        with pytest.raises(FrozenInstanceError):
            pol.user_capabilities = frozenset()  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Rule 1a: untrusted/external content requesting mutating capabilities → DENY
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "cap",
    [
        Capability.WRITE,
        Capability.NETWORK,
        Capability.TOOL_EXEC,
        Capability.ARTIFACT_UPLOAD,
        Capability.DELETE,
    ],
)
def test_untrusted_mutating_cap_denied(cap: Capability) -> None:
    dec = evaluate(_req(cap, trust="external", tainted=True))
    assert dec.allowed is False
    assert dec.reason  # non-empty


# ---------------------------------------------------------------------------
# Rule 1b: untrusted READ → ALLOWED
# ---------------------------------------------------------------------------


def test_untrusted_read_allowed() -> None:
    dec = evaluate(_req(Capability.READ, trust="external", tainted=True))
    assert dec.allowed is True
    assert dec.reason


# ---------------------------------------------------------------------------
# Rule 1c: tainted=True with trust="user" → only READ allowed, WRITE denied
# ---------------------------------------------------------------------------


class TestTaintedUserContent:
    def test_tainted_user_read_allowed(self) -> None:
        dec = evaluate(_req(Capability.READ, trust="user", tainted=True))
        assert dec.allowed is True

    def test_tainted_user_write_denied(self) -> None:
        dec = evaluate(_req(Capability.WRITE, trust="user", tainted=True))
        assert dec.allowed is False
        assert dec.reason

    def test_tainted_user_network_denied(self) -> None:
        dec = evaluate(_req(Capability.NETWORK, trust="user", tainted=True))
        assert dec.allowed is False

    def test_tainted_user_tool_exec_denied(self) -> None:
        dec = evaluate(_req(Capability.TOOL_EXEC, trust="user", tainted=True))
        assert dec.allowed is False

    def test_tainted_user_artifact_upload_denied(self) -> None:
        dec = evaluate(_req(Capability.ARTIFACT_UPLOAD, trust="user", tainted=True))
        assert dec.allowed is False

    def test_tainted_user_delete_denied(self) -> None:
        dec = evaluate(_req(Capability.DELETE, trust="user", tainted=True))
        assert dec.allowed is False


# ---------------------------------------------------------------------------
# Rule 2: untainted user content — policy governs
# ---------------------------------------------------------------------------


class TestUntaintedUserPolicy:
    def test_user_write_allowed_by_default_policy(self) -> None:
        dec = evaluate(_req(Capability.WRITE, trust="user", tainted=False))
        assert dec.allowed is True

    def test_user_read_allowed_by_default_policy(self) -> None:
        dec = evaluate(_req(Capability.READ, trust="user", tainted=False))
        assert dec.allowed is True

    def test_user_delete_allowed_by_default_policy(self) -> None:
        dec = evaluate(_req(Capability.DELETE, trust="user", tainted=False))
        assert dec.allowed is True

    def test_user_network_denied_by_default_policy(self) -> None:
        dec = evaluate(_req(Capability.NETWORK, trust="user", tainted=False))
        assert dec.allowed is False
        assert dec.reason

    def test_user_tool_exec_denied_by_default_policy(self) -> None:
        dec = evaluate(_req(Capability.TOOL_EXEC, trust="user", tainted=False))
        assert dec.allowed is False

    def test_user_artifact_upload_denied_by_default_policy(self) -> None:
        dec = evaluate(_req(Capability.ARTIFACT_UPLOAD, trust="user", tainted=False))
        assert dec.allowed is False


# ---------------------------------------------------------------------------
# Custom policy: operator explicitly grants NETWORK
# ---------------------------------------------------------------------------


class TestCustomPolicy:
    def test_custom_policy_grants_network(self) -> None:
        custom = CapabilityPolicy(
            user_capabilities=frozenset({Capability.READ, Capability.WRITE, Capability.NETWORK})
        )
        dec = evaluate(_req(Capability.NETWORK, trust="user", tainted=False), policy=custom)
        assert dec.allowed is True
        assert dec.reason

    def test_custom_policy_still_denies_tool_exec(self) -> None:
        custom = CapabilityPolicy(
            user_capabilities=frozenset({Capability.READ, Capability.NETWORK})
        )
        dec = evaluate(_req(Capability.TOOL_EXEC, trust="user", tainted=False), policy=custom)
        assert dec.allowed is False


# ---------------------------------------------------------------------------
# Unknown trust string with tainted=False → treated as non-user → READ only
# ---------------------------------------------------------------------------


class TestUnknownTrustNonTainted:
    def test_unknown_trust_read_allowed(self) -> None:
        req = CapabilityRequest(
            capability=Capability.READ, source_trust="some-backend", tainted=False
        )
        dec = evaluate(req)
        assert dec.allowed is True

    def test_unknown_trust_write_denied(self) -> None:
        req = CapabilityRequest(
            capability=Capability.WRITE, source_trust="some-backend", tainted=False
        )
        dec = evaluate(req)
        assert dec.allowed is False
        assert dec.reason

    def test_unknown_trust_network_denied(self) -> None:
        req = CapabilityRequest(
            capability=Capability.NETWORK, source_trust="some-backend", tainted=False
        )
        dec = evaluate(req)
        assert dec.allowed is False


# ---------------------------------------------------------------------------
# Every decision has a non-empty reason (exhaustive for all caps × trust combos)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("cap", list(Capability))
@pytest.mark.parametrize(
    "trust,tainted",
    [
        ("external", True),
        ("user", True),
        ("user", False),
        ("derived", False),
        ("", True),
    ],
)
def test_every_decision_has_non_empty_reason(cap: Capability, trust: str, tainted: bool) -> None:
    req = CapabilityRequest(capability=cap, source_trust=trust, tainted=tainted)
    dec = evaluate(req)
    assert isinstance(dec.reason, str)
    assert len(dec.reason) > 0


# ---------------------------------------------------------------------------
# DEFAULT_POLICY sanity
# ---------------------------------------------------------------------------


class TestDefaultPolicy:
    def test_default_has_read(self) -> None:
        assert Capability.READ in DEFAULT_POLICY.user_capabilities

    def test_default_has_write(self) -> None:
        assert Capability.WRITE in DEFAULT_POLICY.user_capabilities

    def test_default_has_delete(self) -> None:
        assert Capability.DELETE in DEFAULT_POLICY.user_capabilities

    def test_default_lacks_network(self) -> None:
        assert Capability.NETWORK not in DEFAULT_POLICY.user_capabilities

    def test_default_lacks_tool_exec(self) -> None:
        assert Capability.TOOL_EXEC not in DEFAULT_POLICY.user_capabilities

    def test_default_lacks_artifact_upload(self) -> None:
        assert Capability.ARTIFACT_UPLOAD not in DEFAULT_POLICY.user_capabilities
