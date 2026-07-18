"""Concurrency, crash-recovery, and preservation tests for proposal queues."""

from __future__ import annotations

import json
import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

import mneme_core.memory_apply as memory_apply
from mneme_core.approval import EditCategory, MemoryProposal, propose
from mneme_core.memory_apply import EditResult, drain_proposals, queue_proposal
from mneme_core.policy import AutoApproveClass
from mneme_core.vault.config import VaultConfig


@pytest.fixture
def vault(tmp_path: Path) -> VaultConfig:
    config = VaultConfig.from_path(tmp_path)
    config.state_dir.mkdir(parents=True, exist_ok=True)
    return config


def _proposal(index: int) -> MemoryProposal:
    return propose(
        action="create",
        target_path=f"notes/{index}.md",
        content=f"content-{index}",
        category=EditCategory.EPHEMERAL,
    )


def test_concurrent_queue_writers_do_not_lose_records(vault: VaultConfig) -> None:
    with ThreadPoolExecutor(max_workers=12) as pool:
        list(
            pool.map(
                lambda index: queue_proposal(
                    vault, _proposal(index), AutoApproveClass.TYPO_FIX
                ),
                range(100),
            )
        )

    queue = vault.state_dir / "proposals" / "pending.jsonl"
    records = [json.loads(line) for line in queue.read_text(encoding="utf-8").splitlines()]
    assert len(records) == 100
    assert len({record["proposal_id"] for record in records}) == 100


def test_drain_claim_does_not_delete_fresh_queue(
    vault: VaultConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    first = _proposal(1)
    later = _proposal(2)
    queue_proposal(vault, first, AutoApproveClass.TYPO_FIX)
    called = False

    def fake_apply(*args: object, **kwargs: object) -> EditResult:
        nonlocal called
        if not called:
            called = True
            queue_proposal(vault, later, AutoApproveClass.TYPO_FIX)
        return EditResult(True, "change", "applied", "notes/1.md")

    monkeypatch.setattr(memory_apply, "apply_edit", fake_apply)
    report = drain_proposals(vault)

    assert report.applied == 1
    pending = vault.state_dir / "proposals" / "pending.jsonl"
    pending_records = [
        json.loads(line) for line in pending.read_text(encoding="utf-8").splitlines()
    ]
    assert [record["proposal_id"] for record in pending_records] == [later.proposal_id]
    processed = list((pending.parent / "processed").glob("*.processed.jsonl"))
    assert len(processed) == 1
    assert first.proposal_id in processed[0].read_text(encoding="utf-8")


def test_malformed_records_are_preserved(vault: VaultConfig) -> None:
    queue = vault.state_dir / "proposals" / "pending.jsonl"
    queue.parent.mkdir(parents=True, exist_ok=True)
    queue.write_text("{broken\n", encoding="utf-8")

    report = drain_proposals(vault)

    assert report.malformed == 1
    malformed = list((queue.parent / "processed").glob("*.malformed.jsonl"))
    processed = list((queue.parent / "processed").glob("*.processed.jsonl"))
    assert len(malformed) == 1
    assert len(processed) == 1
    assert malformed[0].read_text(encoding="utf-8") == "{broken\n"


def test_stale_lock_is_recovered(vault: VaultConfig) -> None:
    lock = vault.state_dir / "proposals" / "pending.jsonl.lock"
    lock.parent.mkdir(parents=True, exist_ok=True)
    lock.write_text("crashed", encoding="utf-8")
    stale = memory_apply._QUEUE_LOCK_STALE_S + 5.0
    os.utime(lock, (lock.stat().st_atime - stale, lock.stat().st_mtime - stale))

    queue_proposal(vault, _proposal(1), AutoApproveClass.TYPO_FIX)

    assert not lock.exists()


def test_queue_symlink_is_refused(vault: VaultConfig, tmp_path: Path) -> None:
    queue = vault.state_dir / "proposals" / "pending.jsonl"
    queue.parent.mkdir(parents=True, exist_ok=True)
    outside = tmp_path.parent / f"outside-{tmp_path.name}.jsonl"
    outside.write_text("outside\n", encoding="utf-8")
    try:
        queue.symlink_to(outside)
    except OSError:
        pytest.skip("symlink creation is unavailable")

    with pytest.raises(OSError, match="regular file"):
        queue_proposal(vault, _proposal(1), AutoApproveClass.TYPO_FIX)
    assert outside.read_text(encoding="utf-8") == "outside\n"
