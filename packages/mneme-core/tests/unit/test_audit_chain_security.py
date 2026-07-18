"""Security regression tests for the cross-language audit chain."""

from __future__ import annotations

import hashlib
import hmac
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path

import pytest

import mneme_core.audit_chain as audit_chain
from mneme_core.audit_chain import ZERO_HASH, append_chain_record, verify_chain


def _day() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%d")


def _records(state_dir: Path) -> list[dict[str, object]]:
    path = state_dir / "audit" / f"{_day()}.jsonl"
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def _append_typescript_shaped_record(state_dir: Path, relative_path: str) -> None:
    records = _records(state_dir)
    prev_hash = str(records[-1]["hmac"]) if records else ZERO_HASH
    key = (state_dir / "audit-hmac.key").read_bytes()
    body = {
        "timestamp_iso": "2026-07-18T00:00:00.000Z",
        "relative_path": relative_path,
        "redactions_applied": 1,
        "prev_hash": prev_hash,
    }
    serialized = json.dumps(body, separators=(",", ":"), ensure_ascii=False)
    digest = hmac.new(
        key,
        (prev_hash + serialized).encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    chain_path = state_dir / "audit" / f"{_day()}.jsonl"
    with chain_path.open("a", encoding="utf-8", newline="\n") as fp:
        fp.write(
            json.dumps(
                {**body, "hmac": digest},
                separators=(",", ":"),
                ensure_ascii=False,
            )
            + "\n"
        )


def test_concurrent_appends_are_serialized_and_monotonic(tmp_path: Path) -> None:
    state_dir = tmp_path / ".mneme"

    def append(index: int) -> bool:
        return append_chain_record(
            state_dir,
            {"kind": "concurrency", "relative_path": f"notes/{index}.md"},
        )

    with ThreadPoolExecutor(max_workers=8) as pool:
        outcomes = list(pool.map(append, range(24)))

    assert all(outcomes)
    records = _records(state_dir)
    assert [record["sequence"] for record in records] == list(range(1, 25))
    report = verify_chain(state_dir, _day())
    assert report.valid is True
    assert report.records == 24

    seal = json.loads(
        (state_dir / "audit" / f"{_day()}.seal.json").read_text(encoding="utf-8")
    )
    assert seal["sequence"] == 24
    assert seal["head_hmac"] == records[-1]["hmac"]
    assert not (state_dir / "audit" / f"{_day()}.lock").exists()
    assert not (state_dir / "audit" / f"{_day()}.py.lock").exists()


def test_typescript_shaped_record_is_accepted_and_sealed(tmp_path: Path) -> None:
    state_dir = tmp_path / ".mneme"
    audit_dir = state_dir / "audit"
    audit_dir.mkdir(parents=True)
    key = b"k" * 32
    (state_dir / "audit-hmac.key").write_bytes(key)

    body = {
        "timestamp_iso": "2026-07-18T00:00:00.000Z",
        "relative_path": "notes/typescript.md",
        "redactions_applied": 1,
        "prev_hash": ZERO_HASH,
    }
    serialized = json.dumps(body, separators=(",", ":"), ensure_ascii=False)
    digest = hmac.new(
        key,
        (ZERO_HASH + serialized).encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    first = {**body, "hmac": digest}
    (audit_dir / f"{_day()}.jsonl").write_text(
        json.dumps(first, separators=(",", ":"), ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    assert append_chain_record(
        state_dir,
        {"kind": "python", "relative_path": "notes/python.md"},
    )
    records = _records(state_dir)
    assert "sequence" not in records[0]
    assert records[1]["sequence"] == 2
    report = verify_chain(state_dir, _day())
    assert report.valid is True
    assert report.records == 2
    assert report.detail == "chain and seal valid"


def test_typescript_suffix_is_verified_then_included_in_next_seal(tmp_path: Path) -> None:
    state_dir = tmp_path / ".mneme"
    assert append_chain_record(
        state_dir,
        {"kind": "python", "relative_path": "notes/first.md"},
    )
    _append_typescript_shaped_record(state_dir, "notes/typescript.md")

    unsealed_report = verify_chain(state_dir, _day())

    assert unsealed_report.valid is True
    assert unsealed_report.records == 2
    assert "1 unsealed cross-language record" in unsealed_report.detail
    assert append_chain_record(
        state_dir,
        {"kind": "python", "relative_path": "notes/last.md"},
    )
    records = _records(state_dir)
    assert records[-1]["sequence"] == 3
    final_report = verify_chain(state_dir, _day())
    assert final_report.valid is True
    assert final_report.records == 3
    assert final_report.detail == "chain and seal valid"


def test_seal_detects_tail_truncation(tmp_path: Path) -> None:
    state_dir = tmp_path / ".mneme"
    for index in range(3):
        assert append_chain_record(
            state_dir,
            {"kind": "truncate", "relative_path": f"notes/{index}.md"},
        )

    chain_path = state_dir / "audit" / f"{_day()}.jsonl"
    lines = chain_path.read_text(encoding="utf-8").splitlines()
    chain_path.write_text("\n".join(lines[:-1]) + "\n", encoding="utf-8")

    report = verify_chain(state_dir, _day())
    assert report.valid is False
    assert report.records == 2
    assert "tail truncation" in report.detail
    assert append_chain_record(
        state_dir,
        {"kind": "must-fail", "relative_path": "notes/fail.md"},
    ) is False


def test_python_sequenced_chain_rejects_missing_seal(tmp_path: Path) -> None:
    state_dir = tmp_path / ".mneme"
    assert append_chain_record(
        state_dir,
        {"kind": "sealed", "relative_path": "notes/sealed.md"},
    )
    (state_dir / "audit" / f"{_day()}.seal.json").unlink()

    report = verify_chain(state_dir, _day())

    assert report.valid is False
    assert "seal missing" in report.detail
    assert append_chain_record(
        state_dir,
        {"kind": "must-fail", "relative_path": "notes/fail.md"},
    ) is False


def test_seal_write_fault_restores_previous_chain_snapshot(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state_dir = tmp_path / ".mneme"
    assert append_chain_record(
        state_dir,
        {"kind": "first", "relative_path": "notes/first.md"},
    )
    chain_path = state_dir / "audit" / f"{_day()}.jsonl"
    seal_path = state_dir / "audit" / f"{_day()}.seal.json"
    original_chain = chain_path.read_text(encoding="utf-8")
    original_seal = seal_path.read_text(encoding="utf-8")

    def fail_seal(*_args: object, **_kwargs: object) -> None:
        raise OSError("injected seal write failure")

    monkeypatch.setattr(audit_chain, "_write_seal", fail_seal)

    assert append_chain_record(
        state_dir,
        {"kind": "second", "relative_path": "notes/second.md"},
    ) is False
    assert chain_path.read_text(encoding="utf-8") == original_chain
    assert seal_path.read_text(encoding="utf-8") == original_seal
    report = verify_chain(state_dir, _day())
    assert report.valid is True
    assert report.records == 1


def test_foreign_exclusive_lock_blocks_append(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state_dir = tmp_path / ".mneme"
    audit_dir = state_dir / "audit"
    audit_dir.mkdir(parents=True)
    lock_path = audit_dir / f"{_day()}.lock"
    lock_path.write_text("typescript-owner", encoding="utf-8")
    monkeypatch.setattr(audit_chain, "_LOCK_TIMEOUT_S", 0.02)
    monkeypatch.setattr(audit_chain, "_LOCK_POLL_S", 0.001)

    assert append_chain_record(
        state_dir,
        {"kind": "locked", "relative_path": "notes/locked.md"},
    ) is False
    assert lock_path.read_text(encoding="utf-8") == "typescript-owner"
    assert not (audit_dir / f"{_day()}.jsonl").exists()
