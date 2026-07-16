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
import os
import time
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from collections.abc import Iterator
from typing import Any

from .approval import EditCategory, MemoryProposal, ProposalStatus, can_apply
from .audit_chain import append_chain_record
from .policy import AutoApproveClass, PolicyConfig, evaluate, load_policy
from .scope import (
    DEFAULT_SCOPE,
    DocumentScopeError,
    classify_markdown_scope,
    concrete_scope_or_none,
    stamp_markdown_scope,
)
from .vault.atomic_write import atomic_write_text
from .vault.config import VaultConfig

ROLLBACK_DIR_NAME = "rollback"
PROPOSALS_DIR_NAME = "proposals"
PENDING_QUEUE_FILENAME = "pending.jsonl"
MAX_QUEUE_BYTES = 16 * 1024 * 1024
MAX_QUEUE_LINES = 10_000
MAX_QUEUE_RECORD_BYTES = 1 * 1024 * 1024
_QUEUE_LOCK_TIMEOUT_S = 1.0
_QUEUE_LOCK_STALE_S = 10.0

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


@contextlib.contextmanager
def _queue_lock(queue: Path) -> Iterator[None]:
    """Cross-language O_EXCL lock shared with the TypeScript queue writer."""

    lock = queue.with_name(f"{queue.name}.lock")
    deadline = time.monotonic() + _QUEUE_LOCK_TIMEOUT_S
    lock.parent.mkdir(parents=True, exist_ok=True)
    fd: int | None = None
    while fd is None:
        try:
            fd = os.open(lock, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            try:
                if time.time() - lock.stat().st_mtime > _QUEUE_LOCK_STALE_S:
                    lock.unlink(missing_ok=True)
                    continue
            except OSError:
                continue
            if time.monotonic() >= deadline:
                raise TimeoutError("proposal queue is busy")
            time.sleep(0.01)
    try:
        os.write(fd, str(os.getpid()).encode("ascii", errors="ignore"))
        yield
    finally:
        os.close(fd)
        with contextlib.suppress(OSError):
            lock.unlink()


def _claim_queue(queue: Path) -> Path | None:
    """Atomically claim the current queue while allowing new appends."""

    with _queue_lock(queue):
        if not queue.is_file():
            return None
        claimed = queue.with_name(f"processing-{uuid.uuid4().hex}.jsonl")
        os.replace(queue, claimed)
        return claimed


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

    proposal_scope = concrete_scope_or_none(proposal.scope)
    if proposal_scope is None:
        return EditResult(False, None, "proposal scope is invalid", proposal.target_path)

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
        except OSError:
            return EditResult(False, None, "cannot read prior content", proposal.target_path)
        try:
            target_scope = classify_markdown_scope(prior_content).scope
        except DocumentScopeError:
            return EditResult(
                False,
                None,
                "target scope metadata is malformed",
                proposal.target_path,
            )
        if target_scope != proposal_scope:
            return EditResult(
                False,
                None,
                "target is outside the proposal scope",
                proposal.target_path,
            )

    if proposal.action == "create" and prior_content is not None:
        return EditResult(False, None, "create target already exists", proposal.target_path)
    if proposal.action == "update" and prior_content is None:
        return EditResult(False, None, "update target does not exist", proposal.target_path)
    if proposal.action == "delete" and prior_content is None:
        return EditResult(False, None, "delete target does not exist", proposal.target_path)

    new_content: str | None = None
    if proposal.action != "delete":
        try:
            new_content = stamp_markdown_scope(proposal.content, proposal_scope)
        except DocumentScopeError:
            return EditResult(
                False,
                None,
                "proposed content has conflicting scope metadata",
                proposal.target_path,
            )

    prior_hash = _content_hash(prior_content) if prior_content is not None else ""
    change_id = str(uuid.uuid5(_NAMESPACE, f"{proposal.proposal_id}\x00{prior_hash}"))

    journal = {
        "change_id": change_id,
        "proposal_id": proposal.proposal_id,
        "action": proposal.action,
        "path": proposal.target_path,
        "category": proposal.category.value,
        "scope": proposal_scope,
        "edit_class": edit_class.value if edit_class else None,
        "auto_approved": decision.auto_approved and not human_approved,
        "prior_content": prior_content,
        "new_content": new_content,
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
            assert new_content is not None
            atomic_write_text(target, new_content)
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
            "scope": proposal_scope,
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
    scope = concrete_scope_or_none(journal.get("scope", DEFAULT_SCOPE))
    if scope is None:
        return EditResult(False, change_id, "journal scope is invalid", rel_path)
    target = _resolve_target(vault, rel_path)
    if target is None:
        return EditResult(False, change_id, "journal path escapes the vault root", rel_path)

    if target.is_file():
        try:
            current_scope = classify_markdown_scope(target.read_text(encoding="utf-8")).scope
        except (OSError, DocumentScopeError):
            return EditResult(False, change_id, "current target scope cannot be verified", rel_path)
        if current_scope != scope:
            return EditResult(
                False,
                change_id,
                "current target is outside the journal scope",
                rel_path,
            )

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
            "scope": scope,
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
                    "scope",
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
    """Append a bounded, scope-bound proposal for a later policy drain."""

    scope = concrete_scope_or_none(proposal.scope)
    if scope is None:
        raise ValueError("proposal scope must be a concrete valid identifier")
    queue = _queue_path(vault)
    queue.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "proposal_id": proposal.proposal_id,
        "action": proposal.action,
        "target_path": proposal.target_path,
        "content": proposal.content,
        "category": proposal.category.value,
        "scope": scope,
        "status": proposal.status.value,
        "trust": proposal.trust,
        "edit_class": edit_class.value if edit_class else None,
        "queued_at": datetime.now(UTC).isoformat(),
    }
    encoded = (json.dumps(record, ensure_ascii=False) + "\n").encode("utf-8")
    if len(encoded) > MAX_QUEUE_RECORD_BYTES:
        raise ValueError("proposal queue record exceeds the safe size bound")
    with _queue_lock(queue):
        current_size = queue.stat().st_size if queue.is_file() else 0
        if current_size + len(encoded) > MAX_QUEUE_BYTES:
            raise ValueError("proposal queue exceeds the safe size bound")
        with queue.open("ab") as f:
            f.write(encoded)
            f.flush()
            os.fsync(f.fileno())
    return queue


def drain_proposals(vault: VaultConfig) -> DrainReport:
    """Atomically claim and drain one bounded proposal queue snapshot.

    New proposals may be appended to a fresh queue while the claimed snapshot
    is processed. Every claimed record is archived, refused and malformed
    records receive dedicated review archives, and no malformed input widens a
    scope or bypasses policy.
    """

    queue = _queue_path(vault)
    try:
        claimed = _claim_queue(queue)
    except (OSError, TimeoutError):
        return DrainReport(0, 1, 0, [EditResult(False, None, "proposal queue is busy", "")])
    if claimed is None:
        return DrainReport(0, 0, 0, [])

    archive_dir = queue.parent / "processed"
    archive_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S.%f")
    try:
        if claimed.stat().st_size > MAX_QUEUE_BYTES:
            destination = archive_dir / f"{stamp}.oversized.jsonl"
            os.replace(claimed, destination)
            return DrainReport(
                0,
                1,
                1,
                [EditResult(False, None, "proposal queue exceeds the safe size bound", "")],
            )
        raw_text = claimed.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        with contextlib.suppress(OSError):
            os.replace(claimed, archive_dir / f"{stamp}.unreadable.jsonl")
        return DrainReport(
            0,
            1,
            1,
            [EditResult(False, None, "proposal queue is unreadable", "")],
        )

    raw_lines = raw_text.splitlines()
    if len(raw_lines) > MAX_QUEUE_LINES:
        os.replace(claimed, archive_dir / f"{stamp}.oversized.jsonl")
        return DrainReport(
            0,
            1,
            1,
            [EditResult(False, None, "proposal queue has too many records", "")],
        )

    policy = load_policy(vault)
    applied = refused = malformed = 0
    results: list[EditResult] = []
    kept: list[str] = []
    malformed_lines: list[str] = []
    for raw_line in raw_lines:
        raw = raw_line.strip()
        if not raw:
            continue
        if len(raw.encode("utf-8")) > MAX_QUEUE_RECORD_BYTES:
            malformed += 1
            malformed_lines.append(raw)
            continue
        try:
            rec = json.loads(raw)
            scope = concrete_scope_or_none(rec.get("scope", DEFAULT_SCOPE))
            if scope is None:
                raise ValueError("invalid proposal scope")
            proposal = MemoryProposal(
                proposal_id=str(rec["proposal_id"]),
                action=str(rec["action"]),
                target_path=str(rec["target_path"]),
                content=str(rec.get("content") or ""),
                category=EditCategory(str(rec["category"])),
                status=ProposalStatus(str(rec.get("status") or "PENDING")),
                trust=str(rec.get("trust") or "agent"),
                scope=scope,
            )
            raw_class = rec.get("edit_class")
            edit_class = AutoApproveClass(str(raw_class)) if raw_class else None
        except (AttributeError, KeyError, ValueError, TypeError, json.JSONDecodeError):
            malformed += 1
            malformed_lines.append(raw)
            continue
        result = apply_edit(vault, proposal, edit_class, policy=policy)
        results.append(result)
        if result.applied:
            applied += 1
        else:
            refused += 1
            kept.append(raw)

    # Preserve the complete claimed snapshot as provenance before removing it.
    os.replace(claimed, archive_dir / f"{stamp}.processed.jsonl")
    if kept:
        atomic_write_text(archive_dir / f"{stamp}.refused.jsonl", "\n".join(kept) + "\n")
    if malformed_lines:
        atomic_write_text(
            archive_dir / f"{stamp}.malformed.jsonl",
            "\n".join(malformed_lines) + "\n",
        )
    return DrainReport(applied=applied, refused=refused, malformed=malformed, results=results)
