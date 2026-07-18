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
import errno
import json
import os
import stat
import time
import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
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
        except OSError as exc:
            expected_error = exc.errno in {errno.EEXIST, errno.EACCES, errno.EPERM}
            if not expected_error:
                raise
            if not lock.exists():
                continue
            try:
                if time.time() - lock.stat().st_mtime > _QUEUE_LOCK_STALE_S:
                    lock.unlink(missing_ok=True)
                    continue
            except OSError:
                continue
            if time.monotonic() >= deadline:
                raise TimeoutError("proposal queue is busy") from exc
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
        if not queue.exists():
            return None
        queue_stat = queue.lstat()
        if queue.is_symlink() or not stat.S_ISREG(queue_stat.st_mode):
            raise OSError("proposal queue is not a regular file")
        claimed = queue.with_name(f"processing-{uuid.uuid4().hex}.jsonl")
        os.replace(queue, claimed)
        return claimed


def _write_all(fd: int, payload: bytes) -> None:
    """Write *payload* completely to an already-open file descriptor."""
    offset = 0
    while offset < len(payload):
        written = os.write(fd, payload[offset:])
        if written <= 0:
            raise OSError("proposal queue write made no progress")
        offset += written


def _read_regular_file(path: Path, *, max_bytes: int) -> bytes:
    """Read a bounded regular file without following its final symlink."""
    nofollow = getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(path, os.O_RDONLY | nofollow)
    try:
        file_stat = os.fstat(fd)
        if not stat.S_ISREG(file_stat.st_mode):
            raise OSError("proposal queue is not a regular file")
        if file_stat.st_size > max_bytes:
            raise ValueError("proposal queue exceeds the configured size limit")
        chunks: list[bytes] = []
        remaining = max_bytes + 1
        while remaining > 0:
            chunk = os.read(fd, min(1024 * 1024, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        payload = b"".join(chunks)
        if len(payload) > max_bytes:
            raise ValueError("proposal queue exceeds the configured size limit")
        return payload
    finally:
        os.close(fd)


def _resolve_target(vault: VaultConfig, target_path: str) -> Path | None:
    """Resolve *target_path* inside the vault root, or ``None`` on escape."""
    candidate = (vault.root / target_path).resolve()
    root = vault.root.resolve()
    if candidate == root or not candidate.is_relative_to(root):
        return None
    return candidate


def _content_hash(text: str) -> str:
    return sha256(text.encode("utf-8")).hexdigest()


def _write_journal(
    path: Path,
    journal: dict[str, Any],
    *,
    vault_root: Path,
) -> None:
    atomic_write_text(
        path,
        json.dumps(journal, indent=2, ensure_ascii=False) + "\n",
        vault_root=vault_root,
    )


def _restore_prior_state(
    target: Path,
    prior_content: str | None,
    *,
    vault_root: Path,
) -> None:
    if prior_content is None:
        if target.exists():
            if not target.is_file():
                raise OSError("target is no longer a regular file")
            target.unlink()
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(target, prior_content, vault_root=vault_root)


def _record_apply_audit(
    vault: VaultConfig,
    proposal: MemoryProposal,
    edit_class: AutoApproveClass | None,
    *,
    change_id: str,
    auto_approved: bool,
    phase: str,
    post_hash: str,
) -> bool:
    return append_chain_record(
        vault.state_dir,
        {
            "kind": "memory_edit",
            "phase": phase,
            "relative_path": proposal.target_path,
            "change_id": change_id,
            "action": proposal.action,
            "category": proposal.category.value,
            "scope": proposal.scope,
            "edit_class": edit_class.value if edit_class else None,
            "auto_approved": auto_approved,
            "post_hash": post_hash,
        },
    )


def _mark_failed_after_mutation(
    journal_path: Path,
    journal: dict[str, Any],
    target: Path,
    prior_content: str | None,
    *,
    vault_root: Path,
    reason: str,
) -> str:
    try:
        _restore_prior_state(target, prior_content, vault_root=vault_root)
    except OSError as exc:
        journal["status"] = "recovery-required"
        journal["failure_reason"] = reason
        journal["recovery_error"] = str(exc)
        with contextlib.suppress(OSError):
            _write_journal(journal_path, journal, vault_root=vault_root)
        return f"{reason}; automatic restoration failed: {exc}"

    journal["status"] = "failed-restored"
    journal["failure_reason"] = reason
    with contextlib.suppress(OSError):
        _write_journal(journal_path, journal, vault_root=vault_root)
    return f"{reason}; prior state restored"


def _expected_post_state(journal: dict[str, Any]) -> tuple[bool, str] | None:
    if "post_exists" in journal or "post_hash" in journal:
        exists = journal.get("post_exists")
        post_hash = journal.get("post_hash")
        if not isinstance(exists, bool) or not isinstance(post_hash, str):
            return None
        if exists and len(post_hash) != 64:
            return None
        if not exists and post_hash:
            return None
        return exists, post_hash

    action = journal.get("action")
    if action == "delete":
        return False, ""
    new_content = journal.get("new_content")
    if not isinstance(new_content, str):
        return None
    return True, _content_hash(new_content)


def _current_state_matches(
    target: Path,
    *,
    expected_exists: bool,
    expected_hash: str,
) -> tuple[bool, str]:
    if not expected_exists:
        if target.exists():
            return False, "target was recreated after the recorded delete"
        return True, ""
    if not target.is_file():
        return False, "target is missing or is not a regular file"
    try:
        current_hash = _content_hash(target.read_text(encoding="utf-8"))
    except OSError as exc:
        return False, f"current target cannot be read: {exc}"
    if current_hash != expected_hash:
        return False, "current target hash differs from the applied journal state"
    return True, ""


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

    post_exists = proposal.action != "delete"
    post_hash = _content_hash(new_content) if new_content is not None else ""
    auto_approved = decision.auto_approved and not human_approved
    journal: dict[str, Any] = {
        "change_id": change_id,
        "proposal_id": proposal.proposal_id,
        "action": proposal.action,
        "path": proposal.target_path,
        "category": proposal.category.value,
        "scope": proposal_scope,
        "edit_class": edit_class.value if edit_class else None,
        "auto_approved": auto_approved,
        "prior_content": prior_content,
        "new_content": new_content,
        "prior_exists": prior_content is not None,
        "prior_hash": prior_hash,
        "post_exists": post_exists,
        "post_hash": post_hash,
        "applied_at": datetime.now(UTC).isoformat(),
        "status": "prepared",
    }
    rollback_dir = _rollback_dir(vault)
    journal_path = rollback_dir / f"{change_id}.json"
    try:
        rollback_dir.mkdir(parents=True, exist_ok=True)
        _write_journal(journal_path, journal, vault_root=vault.root)
    except OSError as exc:
        return EditResult(
            False,
            change_id,
            f"cannot prepare rollback journal: {exc}",
            proposal.target_path,
        )

    if auto_approved and not _record_apply_audit(
        vault,
        proposal,
        edit_class,
        change_id=change_id,
        auto_approved=True,
        phase="prepare",
        post_hash=post_hash,
    ):
        journal["status"] = "audit-failed"
        journal["failure_reason"] = "prepare audit append failed"
        with contextlib.suppress(OSError):
            _write_journal(journal_path, journal, vault_root=vault.root)
        return EditResult(
            False,
            change_id,
            "audit unavailable; autonomous edit refused",
            proposal.target_path,
        )

    try:
        if proposal.action == "delete":
            target.unlink()
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            assert new_content is not None
            atomic_write_text(target, new_content, vault_root=vault.root)
    except OSError as exc:
        journal["status"] = "failed"
        journal["failure_reason"] = str(exc)
        with contextlib.suppress(OSError):
            _write_journal(journal_path, journal, vault_root=vault.root)
        if auto_approved:
            _record_apply_audit(
                vault,
                proposal,
                edit_class,
                change_id=change_id,
                auto_approved=True,
                phase="abort",
                post_hash=post_hash,
            )
        return EditResult(False, change_id, f"apply failed: {exc}", proposal.target_path)

    journal["status"] = "applied"
    try:
        _write_journal(journal_path, journal, vault_root=vault.root)
    except OSError as exc:
        reason = _mark_failed_after_mutation(
            journal_path,
            journal,
            target,
            prior_content,
            vault_root=vault.root,
            reason=f"cannot finalize rollback journal: {exc}",
        )
        if auto_approved:
            _record_apply_audit(
                vault,
                proposal,
                edit_class,
                change_id=change_id,
                auto_approved=True,
                phase="abort",
                post_hash=post_hash,
            )
        return EditResult(False, change_id, reason, proposal.target_path)

    audit_ok = _record_apply_audit(
        vault,
        proposal,
        edit_class,
        change_id=change_id,
        auto_approved=auto_approved,
        phase="commit",
        post_hash=post_hash,
    )
    if auto_approved and not audit_ok:
        reason = _mark_failed_after_mutation(
            journal_path,
            journal,
            target,
            prior_content,
            vault_root=vault.root,
            reason="commit audit append failed",
        )
        _record_apply_audit(
            vault,
            proposal,
            edit_class,
            change_id=change_id,
            auto_approved=True,
            phase="abort",
            post_hash=post_hash,
        )
        return EditResult(False, change_id, reason, proposal.target_path)
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
    if journal.get("status") != "applied":
        return EditResult(
            False,
            change_id,
            "change is not in an applied state",
            str(journal.get("path", "")),
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
            current_scope = classify_markdown_scope(
                target.read_text(encoding="utf-8")
            ).scope
        except (OSError, DocumentScopeError):
            return EditResult(
                False, change_id, "current target scope cannot be verified", rel_path
            )
        if current_scope != scope:
            return EditResult(
                False,
                change_id,
                "current target is outside the journal scope",
                rel_path,
            )

    expected = _expected_post_state(journal)
    if expected is None:
        return EditResult(
            False,
            change_id,
            "journal does not contain a valid applied state hash",
            rel_path,
        )
    matches, mismatch = _current_state_matches(
        target,
        expected_exists=expected[0],
        expected_hash=expected[1],
    )
    if not matches:
        return EditResult(
            False,
            change_id,
            f"rollback refused: {mismatch}",
            rel_path,
        )

    prior = journal.get("prior_content")
    try:
        if prior is None:
            if target.is_file():
                target.unlink()
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            atomic_write_text(target, str(prior), vault_root=vault.root)
    except OSError as exc:
        return EditResult(False, change_id, f"rollback failed: {exc}", rel_path)

    journal["status"] = "rolled-back"
    journal["rolled_back_at"] = datetime.now(UTC).isoformat()
    _write_journal(journal_path, journal, vault_root=vault.root)
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
    """Append *proposal* to the pending queue for a later policy drain.

    This is the surface MCP clients use: the server stages the
    (already-redacted) proposal; nothing touches vault markdown until
    ``drain_proposals`` applies it under the operator's policy.
    """
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
    payload = (json.dumps(record, ensure_ascii=False) + "\n").encode("utf-8")
    if len(payload) > MAX_QUEUE_RECORD_BYTES:
        raise ValueError("proposal queue record exceeds the configured size limit")

    with _queue_lock(queue):
        if queue.exists():
            queue_stat = queue.lstat()
            if queue.is_symlink() or not stat.S_ISREG(queue_stat.st_mode):
                raise OSError("proposal queue is not a regular file")
            if queue_stat.st_size + len(payload) > MAX_QUEUE_BYTES:
                raise ValueError("proposal queue exceeds the configured size limit")
            existing = _read_regular_file(queue, max_bytes=MAX_QUEUE_BYTES)
            if existing.count(b"\n") >= MAX_QUEUE_LINES:
                raise ValueError("proposal queue exceeds the configured line limit")

        nofollow = getattr(os, "O_NOFOLLOW", 0)
        fd = os.open(
            queue,
            os.O_WRONLY | os.O_APPEND | os.O_CREAT | nofollow,
            0o600,
        )
        try:
            queue_stat = os.fstat(fd)
            if not stat.S_ISREG(queue_stat.st_mode):
                raise OSError("proposal queue is not a regular file")
            if queue_stat.st_size + len(payload) > MAX_QUEUE_BYTES:
                raise ValueError("proposal queue exceeds the configured size limit")
            _write_all(fd, payload)
            os.fsync(fd)
        finally:
            os.close(fd)
    return queue


def drain_proposals(vault: VaultConfig) -> DrainReport:
    """Apply every queued proposal under the current policy, then archive.

    Refused and malformed proposals are preserved in separate archive files
    so a human can inspect them. The complete claimed snapshot is archived as
    well. Writers can create a fresh pending queue while the claimed snapshot
    is processed. Deterministic file IO only.
    """
    queue = _queue_path(vault)
    claimed = _claim_queue(queue)
    if claimed is None:
        return DrainReport(0, 0, 0, [])

    archive_dir = queue.parent / "processed"
    archive_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    archive_base = archive_dir / f"{stamp}-{uuid.uuid4().hex}"

    try:
        payload = _read_regular_file(claimed, max_bytes=MAX_QUEUE_BYTES)
        text = payload.decode("utf-8")
    except (OSError, UnicodeDecodeError, ValueError):
        os.replace(claimed, archive_base.with_suffix(".malformed.jsonl"))
        return DrainReport(0, 0, 1, [])

    lines = text.splitlines()
    if len(lines) > MAX_QUEUE_LINES:
        os.replace(claimed, archive_base.with_suffix(".malformed.jsonl"))
        return DrainReport(0, 0, 1, [])

    policy = load_policy(vault)
    applied = refused = malformed = 0
    results: list[EditResult] = []
    kept: list[str] = []
    malformed_records: list[str] = []
    for raw in lines:
        raw = raw.strip()
        if not raw:
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
        except (KeyError, ValueError, TypeError):
            malformed += 1
            malformed_records.append(raw)
            continue
        result = apply_edit(vault, proposal, edit_class, policy=policy)
        results.append(result)
        if result.applied:
            applied += 1
        else:
            refused += 1
            kept.append(raw)

    if kept:
        atomic_write_text(
            archive_base.with_suffix(".refused.jsonl"),
            "\n".join(kept) + "\n",
            vault_root=vault.root,
        )
    if malformed_records:
        atomic_write_text(
            archive_base.with_suffix(".malformed.jsonl"),
            "\n".join(malformed_records) + "\n",
            vault_root=vault.root,
        )
    os.replace(claimed, archive_base.with_suffix(".processed.jsonl"))
    return DrainReport(applied=applied, refused=refused, malformed=malformed, results=results)
