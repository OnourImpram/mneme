"""Autonomous memory-edit application with rollback and audit chain.

This is the accountable-autonomy engine (conflict-resolution #4,
policy-graduated): an agent's :class:`~mneme_core.approval.MemoryProposal`
is applied to the vault **only** when the human approval flow allows it
(``can_apply``) AND, for the autonomous path, the operator's policy
(:mod:`mneme_core.policy`) auto-approves the declared edit class.

Every applied edit:

* journals the prior state to ``<state_dir>/rollback/<change_id>.json``
  so ``rollback_change`` can restore it with one command;
* appends a tamper-evident record to the shared HMAC audit chain
  (:mod:`mneme_core.audit_chain`), interleaving with the TS writer.

Agents that cannot call this module directly (e.g. the MCP server)
queue proposals as JSONL under ``<state_dir>/proposals/pending.jsonl``;
``drain_proposals`` applies the queue per policy and archives it. The
drain is deterministic file IO — no LLM, no network — so it is safe on
the SessionEnd path.

Path containment is enforced: a proposal whose target resolves outside
the vault root is refused, mirroring the TS ``assertWithinVault``.
"""

from __future__ import annotations

import contextlib
import json
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from typing import Any

from .approval import EditCategory, MemoryProposal, ProposalStatus, can_apply
from .audit_chain import append_chain_record
from .policy import AutoApproveClass, PolicyConfig, evaluate, load_policy
from .vault.atomic_write import atomic_write_text
from .vault.config import VaultConfig

ROLLBACK_DIR_NAME = "rollback"
PROPOSALS_DIR_NAME = "proposals"
PENDING_QUEUE_FILENAME = "pending.jsonl"

_NAMESPACE = uuid.UUID("6ba7b810-9dad-11d1-80b4-00c04fd430c8")


@dataclass(frozen=True)
class EditResult:
    """Outcome of one apply or rollback attempt."""

    applied: bool
    change_id: str | None
    reason: str
    path: str


@dataclass(frozen=True)
class DrainReport:
    """Outcome of draining the pending proposal queue."""

    applied: int
    refused: int
    malformed: int
    results: list[EditResult]


def _rollback_dir(vault: VaultConfig) -> Path:
    return vault.state_dir / ROLLBACK_DIR_NAME


def _queue_path(vault: VaultConfig) -> Path:
    return vault.state_dir / PROPOSALS_DIR_NAME / PENDING_QUEUE_FILENAME


def _resolve_target(vault: VaultConfig, target_path: str) -> Path | None:
    """Resolve *target_path* inside the vault root, or ``None`` on escape."""
    candidate = (vault.root / target_path).resolve()
    root = vault.root.resolve()
    if candidate == root or not candidate.is_relative_to(root):
        return None
    return candidate


def _content_hash(text: str) -> str:
    return sha256(text.encode("utf-8")).hexdigest()


def apply_edit(
    vault: VaultConfig,
    proposal: MemoryProposal,
    edit_class: AutoApproveClass | None,
    *,
    policy: PolicyConfig | None = None,
) -> EditResult:
    """Apply *proposal* to the vault under the accountable-autonomy rules.

    An edit is applied when either the proposal is human-APPROVED, or it
    is applicable per ``can_apply`` AND the policy auto-approves the
    declared *edit_class*. Durable categories therefore never apply
    autonomously: ``evaluate`` refuses them and ``can_apply`` already
    blocks PENDING durable proposals.
    """
    resolved_policy = policy if policy is not None else load_policy(vault)
    human_approved = proposal.status == ProposalStatus.APPROVED
    decision = evaluate(proposal.category, edit_class, resolved_policy)
    if not human_approved:
        if not can_apply(proposal):
            return EditResult(
                False, None, "approval gate refuses this proposal", proposal.target_path
            )
        if not decision.auto_approved:
            return EditResult(False, None, decision.reason, proposal.target_path)

    target = _resolve_target(vault, proposal.target_path)
    if target is None:
        return EditResult(False, None, "target path escapes the vault root", proposal.target_path)
    if proposal.action not in ("create", "update", "delete"):
        return EditResult(
            False, None, f"unknown action {proposal.action!r}", proposal.target_path
        )

    prior_content: str | None = None
    if target.is_file():
        try:
            prior_content = target.read_text(encoding="utf-8")
        except OSError as exc:
            return EditResult(
                False, None, f"cannot read prior content: {exc}", proposal.target_path
            )
    if proposal.action == "delete" and prior_content is None:
        return EditResult(False, None, "delete target does not exist", proposal.target_path)

    prior_hash = _content_hash(prior_content) if prior_content is not None else ""
    change_id = str(uuid.uuid5(_NAMESPACE, f"{proposal.proposal_id}\x00{prior_hash}"))

    journal = {
        "change_id": change_id,
        "proposal_id": proposal.proposal_id,
        "action": proposal.action,
        "path": proposal.target_path,
        "category": proposal.category.value,
        "edit_class": edit_class.value if edit_class else None,
        "auto_approved": decision.auto_approved and not human_approved,
        "prior_content": prior_content,
        "new_content": None if proposal.action == "delete" else proposal.content,
        "applied_at": datetime.now(UTC).isoformat(),
        "status": "applied",
    }
    rollback_dir = _rollback_dir(vault)
    rollback_dir.mkdir(parents=True, exist_ok=True)
    journal_path = rollback_dir / f"{change_id}.json"
    atomic_write_text(journal_path, json.dumps(journal, indent=2, ensure_ascii=False) + "\n")

    try:
        if proposal.action == "delete":
            target.unlink()
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            atomic_write_text(target, proposal.content)
    except OSError as exc:
        journal["status"] = "failed"
        atomic_write_text(journal_path, json.dumps(journal, indent=2, ensure_ascii=False) + "\n")
        return EditResult(False, change_id, f"apply failed: {exc}", proposal.target_path)

    append_chain_record(
        vault.state_dir,
        {
            "kind": "memory_edit",
            "relative_path": proposal.target_path,
            "change_id": change_id,
            "action": proposal.action,
            "category": proposal.category.value,
            "edit_class": edit_class.value if edit_class else None,
            "auto_approved": journal["auto_approved"],
        },
    )
    return EditResult(True, change_id, "applied", proposal.target_path)


def rollback_change(vault: VaultConfig, change_id: str) -> EditResult:
    """Restore the prior state journalled for *change_id*.

    Creates restore the deleted file, updates restore prior content, and
    rolled-back creates are removed. Idempotence: a journal already in
    ``rolled-back`` status is refused so a double rollback cannot
    clobber newer edits.
    """
    journal_path = _rollback_dir(vault) / f"{change_id}.json"
    if not journal_path.is_file():
        return EditResult(False, change_id, "no journal for change_id", "")
    try:
        journal = json.loads(journal_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return EditResult(False, change_id, f"journal unreadable: {exc}", "")
    if journal.get("status") == "rolled-back":
        return EditResult(
            False, change_id, "change already rolled back", str(journal.get("path", ""))
        )

    rel_path = str(journal.get("path") or "")
    target = _resolve_target(vault, rel_path)
    if target is None:
        return EditResult(False, change_id, "journal path escapes the vault root", rel_path)

    prior = journal.get("prior_content")
    try:
        if prior is None:
            if target.is_file():
                target.unlink()
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            atomic_write_text(target, str(prior))
    except OSError as exc:
        return EditResult(False, change_id, f"rollback failed: {exc}", rel_path)

    journal["status"] = "rolled-back"
    journal["rolled_back_at"] = datetime.now(UTC).isoformat()
    atomic_write_text(journal_path, json.dumps(journal, indent=2, ensure_ascii=False) + "\n")
    append_chain_record(
        vault.state_dir,
        {
            "kind": "rollback",
            "relative_path": rel_path,
            "change_id": change_id,
            "action": "rollback",
            "category": str(journal.get("category") or ""),
            "edit_class": journal.get("edit_class"),
            "auto_approved": False,
        },
    )
    return EditResult(True, change_id, "rolled back", rel_path)


def list_changes(vault: VaultConfig) -> list[dict[str, Any]]:
    """Return all rollback journal entries, newest first, content omitted."""
    rollback_dir = _rollback_dir(vault)
    if not rollback_dir.is_dir():
        return []
    entries: list[dict[str, Any]] = []
    for journal_path in rollback_dir.glob("*.json"):
        try:
            journal = json.loads(journal_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(journal, dict):
            continue
        entries.append(
            {
                k: journal.get(k)
                for k in (
                    "change_id",
                    "proposal_id",
                    "action",
                    "path",
                    "category",
                    "edit_class",
                    "auto_approved",
                    "applied_at",
                    "status",
                )
            }
        )
    entries.sort(key=lambda e: str(e.get("applied_at") or ""), reverse=True)
    return entries


def queue_proposal(
    vault: VaultConfig,
    proposal: MemoryProposal,
    edit_class: AutoApproveClass | None,
) -> Path:
    """Append *proposal* to the pending queue for a later policy drain.

    This is the surface MCP clients use: the server stages the
    (already-redacted) proposal; nothing touches vault markdown until
    ``drain_proposals`` applies it under the operator's policy.
    """
    queue = _queue_path(vault)
    queue.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "proposal_id": proposal.proposal_id,
        "action": proposal.action,
        "target_path": proposal.target_path,
        "content": proposal.content,
        "category": proposal.category.value,
        "status": proposal.status.value,
        "trust": proposal.trust,
        "edit_class": edit_class.value if edit_class else None,
        "queued_at": datetime.now(UTC).isoformat(),
    }
    with queue.open("a", encoding="utf-8", newline="") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")
    return queue


def drain_proposals(vault: VaultConfig) -> DrainReport:
    """Apply every queued proposal under the current policy, then archive.

    Refused proposals are preserved in the archive file (suffix
    ``.refused.jsonl``) so a human can review and approve them later;
    the pending queue is emptied either way. Malformed lines are counted
    and dropped. Deterministic file IO only.
    """
    queue = _queue_path(vault)
    if not queue.is_file():
        return DrainReport(0, 0, 0, [])
    policy = load_policy(vault)
    applied = refused = malformed = 0
    results: list[EditResult] = []
    kept: list[str] = []
    for raw in queue.read_text(encoding="utf-8").splitlines():
        raw = raw.strip()
        if not raw:
            continue
        try:
            rec = json.loads(raw)
            proposal = MemoryProposal(
                proposal_id=str(rec["proposal_id"]),
                action=str(rec["action"]),
                target_path=str(rec["target_path"]),
                content=str(rec.get("content") or ""),
                category=EditCategory(str(rec["category"])),
                status=ProposalStatus(str(rec.get("status") or "PENDING")),
                trust=str(rec.get("trust") or "agent"),
            )
            raw_class = rec.get("edit_class")
            edit_class = AutoApproveClass(str(raw_class)) if raw_class else None
        except (KeyError, ValueError, TypeError):
            malformed += 1
            continue
        result = apply_edit(vault, proposal, edit_class, policy=policy)
        results.append(result)
        if result.applied:
            applied += 1
        else:
            refused += 1
            kept.append(raw)

    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S")
    archive_dir = queue.parent / "processed"
    archive_dir.mkdir(parents=True, exist_ok=True)
    if kept:
        atomic_write_text(
            archive_dir / f"{stamp}.refused.jsonl", "\n".join(kept) + "\n"
        )
    with contextlib.suppress(OSError):
        queue.unlink()
    return DrainReport(applied=applied, refused=refused, malformed=malformed, results=results)
