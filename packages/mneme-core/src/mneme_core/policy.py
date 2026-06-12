"""Policy-graduated autonomy for memory edits.

The approval gate (``approval.py``) answers "may this proposal ever be
applied". This module answers the narrower autonomous-path question:
"may the agent apply it **without a human in the loop, right now**".

The operator declares which low-risk edit classes the agent may apply
autonomously in ``<vault>/.mneme/policy.json``::

    {
      "auto_approve": ["dedup-merge", "typo-fix", "tag-normalize",
                        "supersede-link", "stale-archive"],
      "clinical_lock": false
    }

Safety posture, in order of precedence:

1. **Absent file = zero autonomy.** No policy file means nothing is
   auto-approved; every autonomous apply is refused.
2. **Durable categories are never autonomous.** IDENTITY, PREFERENCE,
   CLINICAL, LEGAL, FINANCIAL always require the human approval flow
   regardless of policy content (PA6).
3. **clinical_lock beats everything.** When set, even ephemeral edits
   are refused; clinical-research vaults run with the lock on.
4. **Unknown class strings are ignored**, never an error: a typo in the
   policy file fails closed.

Pure and deterministic apart from the single config read; no LLM, no
network, no clock.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from enum import Enum

from .approval import EditCategory, requires_human_approval
from .vault.config import VaultConfig

POLICY_CONFIG_FILENAME = "policy.json"


class AutoApproveClass(str, Enum):  # noqa: UP042
    """Low-risk mechanical edit classes eligible for autonomous apply.

    Values are the kebab-case strings operators write in policy.json.
    ``UP042`` suppresses the StrEnum suggestion for consistency with the
    ``str`` + ``Enum`` pattern used across the codebase.
    """

    DEDUP_MERGE = "dedup-merge"
    TYPO_FIX = "typo-fix"
    TAG_NORMALIZE = "tag-normalize"
    SUPERSEDE_LINK = "supersede-link"
    STALE_ARCHIVE = "stale-archive"


@dataclass(frozen=True)
class PolicyConfig:
    """Resolved autonomy policy. The default is zero autonomy."""

    allowed_classes: frozenset[AutoApproveClass] = frozenset()
    clinical_lock: bool = False


@dataclass(frozen=True)
class Decision:
    """Outcome of a policy evaluation, with an audit-ready reason."""

    auto_approved: bool
    reason: str


def load_policy(vault: VaultConfig) -> PolicyConfig:
    """Read ``policy.json`` from the vault state dir.

    Absent or malformed file returns the zero-autonomy default. Unknown
    class strings are dropped silently (fail closed). Never raises.
    """
    path = vault.state_dir / POLICY_CONFIG_FILENAME
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return PolicyConfig()
    if not isinstance(raw, dict):
        return PolicyConfig()
    allowed: set[AutoApproveClass] = set()
    raw_classes = raw.get("auto_approve")
    if isinstance(raw_classes, list):
        for item in raw_classes:
            try:
                allowed.add(AutoApproveClass(str(item)))
            except ValueError:
                continue
    return PolicyConfig(
        allowed_classes=frozenset(allowed),
        clinical_lock=bool(raw.get("clinical_lock", False)),
    )


def default_policy_payload() -> dict[str, object]:
    """Template payload written by ``mneme memory policy init``.

    Ships with an EMPTY allow-list: creating the file changes nothing
    until the operator consciously opts classes in. JSON has no
    comments, so the guidance lives in a ``_docs`` key that
    :func:`load_policy` ignores like any other unknown key.
    """
    return {
        "_docs": (
            "Allow-list of edit classes the agent may apply without a "
            "human. Known classes: "
            + ", ".join(c.value for c in AutoApproveClass)
            + ". Durable categories (identity, preference, clinical, "
            "legal, financial) always require human approval regardless "
            "of this file. clinical_lock=true refuses every autonomous "
            "edit."
        ),
        "auto_approve": [],
        "clinical_lock": False,
    }


def inspect_policy(vault: VaultConfig) -> dict[str, object]:
    """Structured validation report for ``policy.json``.

    :func:`load_policy` drops unknown class strings silently (fail
    closed), which is the right runtime posture but hides typos: a
    misspelled class grants nothing and nobody notices. This report
    surfaces them. ``valid`` describes the parse level only; callers
    decide whether unknown classes are fatal.
    """
    path = vault.state_dir / POLICY_CONFIG_FILENAME
    report: dict[str, object] = {"config_path": str(path)}
    if not path.is_file():
        report.update(exists=False, valid=True, note="absent file = zero autonomy")
        return report
    report["exists"] = True
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError) as exc:
        report.update(valid=False, error=f"unreadable or invalid JSON: {exc}")
        return report
    if not isinstance(raw, dict):
        report.update(valid=False, error="top-level value must be an object")
        return report
    raw_classes = raw.get("auto_approve")
    if raw_classes is not None and not isinstance(raw_classes, list):
        report.update(valid=False, error="auto_approve must be a list")
        return report
    known: list[str] = []
    unknown: list[str] = []
    for item in raw_classes or []:
        value = str(item)
        try:
            AutoApproveClass(value)
        except ValueError:
            unknown.append(value)
        else:
            known.append(value)
    report.update(
        valid=True,
        auto_approve=sorted(known),
        unknown_classes=sorted(unknown),
        clinical_lock=bool(raw.get("clinical_lock", False)),
    )
    if unknown:
        report["note"] = "unknown classes are ignored at load time (fail closed)"
    return report


def evaluate(
    category: EditCategory,
    edit_class: AutoApproveClass | None,
    policy: PolicyConfig,
) -> Decision:
    """Decide whether an edit may be applied autonomously.

    The rules are strictly conjunctive: the category must be
    non-durable, the clinical lock must be off, an edit class must be
    declared, and that class must be in the operator's allow-list.
    """
    if policy.clinical_lock:
        return Decision(False, "clinical_lock active: all autonomous edits refused")
    if requires_human_approval(category):
        return Decision(
            False,
            f"durable category {category.value} always requires human approval",
        )
    if edit_class is None:
        return Decision(False, "no edit class declared; autonomous apply refused")
    if edit_class not in policy.allowed_classes:
        return Decision(
            False,
            f"edit class {edit_class.value} not in the policy allow-list",
        )
    return Decision(True, f"policy allows autonomous {edit_class.value}")
