"""Python side of the tamper-evident HMAC audit chain.

This is the kernel-shared counterpart of ``packages/mneme-mcp/src/audit.ts``
and uses the **same** key file, daily JSONL layout, and chaining rule, so
records appended from Python and TypeScript interleave into one verifiable
chain:

* key:   ``<state_dir>/audit-hmac.key`` (32 random bytes, 0o600 on POSIX)
* file:  ``<state_dir>/audit/YYYY-MM-DD.jsonl`` (UTC calendar date)
* chain: ``hmac = HMAC-SHA256(key, prev_hash + serialized_record_without_hmac)``
  where ``prev_hash`` is the previous record's ``hmac`` (64 hex chars) or
  the all-zeros string for the first record, and serialization is compact
  JSON in insertion order (matching ``JSON.stringify``).

Appends are serialized under the shared ``file_lock`` helper; the lock
file mirrors the TS side's per-day lock path. Append failures are
NON-FATAL by contract: callers receive ``False`` and continue.

``verify_chain`` walks a daily file and recomputes every link so doctor
checks and tests can prove the chain has not been edited, reordered, or
truncated in the middle.

No LLM, no network (Core Invariant 2).
"""

from __future__ import annotations

import hmac as _hmac
import json
import os
import secrets
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from typing import Any

from .vault.atomic_write import atomic_write_text
from .vault.file_lock import file_lock

KEY_BYTES = 32
ZERO_HASH = "0" * 64
_LOCK_TIMEOUT_S = 0.5

KEY_FILENAME = "audit-hmac.key"
AUDIT_DIR_NAME = "audit"


def _load_or_create_key(state_dir: Path) -> bytes:
    """Load the chain key, creating it exclusively at 0o600 when absent.

    Mirrors the TS ``loadOrCreateKey``: O_CREAT|O_EXCL so the key is never
    visible at a wider mode even momentarily; an EEXIST race reads the
    winner's key. On Windows the mode bits are advisory (NTFS ACLs govern).
    """
    key_path = state_dir / KEY_FILENAME
    if key_path.is_file():
        data = key_path.read_bytes()
        if len(data) != KEY_BYTES:
            raise ValueError(
                f"{KEY_FILENAME} is {len(data)} bytes; expected {KEY_BYTES}. "
                f"Delete {key_path} to regenerate."
            )
        return data
    state_dir.mkdir(parents=True, exist_ok=True)
    key = secrets.token_bytes(KEY_BYTES)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    # Windows os.open defaults to text mode, which would expand a 0x0A
    # byte inside the random key to CRLF and corrupt it (33-byte key).
    flags |= getattr(os, "O_BINARY", 0)
    try:
        fd = os.open(key_path, flags, 0o600)
    except FileExistsError:
        return key_path.read_bytes()
    try:
        os.write(fd, key)
    finally:
        os.close(fd)
    return key


def _read_last_hmac(jsonl_path: Path) -> str:
    if not jsonl_path.is_file():
        return ZERO_HASH
    try:
        lines = [
            ln
            for ln in jsonl_path.read_text(encoding="utf-8").splitlines()
            if ln.strip()
        ]
    except OSError:
        return ZERO_HASH
    if not lines:
        return ZERO_HASH
    try:
        parsed = json.loads(lines[-1])
    except json.JSONDecodeError:
        return ZERO_HASH
    h = parsed.get("hmac") if isinstance(parsed, dict) else None
    if isinstance(h, str) and len(h) == 64:
        return h
    return ZERO_HASH


def _compute_hmac(key: bytes, prev_hash: str, serialized: str) -> str:
    return _hmac.new(key, (prev_hash + serialized).encode("utf-8"), sha256).hexdigest()


def _serialize(record: dict[str, Any]) -> str:
    """Compact insertion-order JSON, byte-identical to ``JSON.stringify``
    for the str/int/bool/None payloads this chain carries."""
    return json.dumps(record, separators=(",", ":"), ensure_ascii=False)


def append_chain_record(state_dir: Path, record: dict[str, Any]) -> bool:
    """Append one tamper-evident record to today's chain file.

    ``timestamp_iso``, ``prev_hash``, and ``hmac`` are injected here; the
    caller provides the payload fields (e.g. ``kind``, ``relative_path``,
    ``change_id``). Returns ``True`` on success; failures are non-fatal
    and reported as ``False`` with a stderr warning, matching the TS
    contract that audit must never block the operation it describes.
    """
    try:
        audit_dir = state_dir / AUDIT_DIR_NAME
        audit_dir.mkdir(parents=True, exist_ok=True)
        key = _load_or_create_key(state_dir)
        today = datetime.now(UTC).strftime("%Y-%m-%d")
        jsonl_path = audit_dir / f"{today}.jsonl"
        lock_path = audit_dir / f"{today}.py.lock"
        with file_lock(lock_path, timeout_s=_LOCK_TIMEOUT_S):
            prev_hash = _read_last_hmac(jsonl_path)
            without_hmac: dict[str, Any] = {
                "timestamp_iso": datetime.now(UTC).isoformat(),
                **record,
                "prev_hash": prev_hash,
            }
            serialized = _serialize(without_hmac)
            digest = _compute_hmac(key, prev_hash, serialized)
            full = {**without_hmac, "hmac": digest}
            existing = (
                jsonl_path.read_text(encoding="utf-8") if jsonl_path.is_file() else ""
            )
            atomic_write_text(jsonl_path, existing + _serialize(full) + "\n")
        return True
    except Exception as exc:  # noqa: BLE001 - audit append is non-fatal by contract
        sys.stderr.write(f"[mneme audit] chain append failed: {exc}\n")
        return False


@dataclass(frozen=True)
class ChainReport:
    """Result of verifying one daily chain file."""

    records: int
    valid: bool
    first_break_line: int | None = None
    detail: str = ""


def verify_chain(state_dir: Path, day: str) -> ChainReport:
    """Recompute every link of the ``day`` (``YYYY-MM-DD``) chain file.

    A record verifies when (a) its ``prev_hash`` equals the previous
    record's ``hmac`` (ZERO_HASH for the first), and (b) its ``hmac``
    recomputes from the record serialized without the ``hmac`` field.
    Verification is serialization-sensitive by design: the hmac is
    recomputed over the exact line bytes minus the hmac field, so a
    re-serialized (edited) line breaks the chain even if semantically
    equal.
    """
    jsonl_path = state_dir / AUDIT_DIR_NAME / f"{day}.jsonl"
    if not jsonl_path.is_file():
        return ChainReport(records=0, valid=True, detail="no chain file")
    try:
        key = _load_or_create_key(state_dir)
    except (OSError, ValueError) as exc:
        return ChainReport(records=0, valid=False, detail=f"key unavailable: {exc}")
    prev = ZERO_HASH
    count = 0
    for lineno, raw in enumerate(
        jsonl_path.read_text(encoding="utf-8").splitlines(), start=1
    ):
        raw = raw.strip()
        if not raw:
            continue
        count += 1
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            return ChainReport(count, False, lineno, "unparseable record")
        if not isinstance(parsed, dict):
            return ChainReport(count, False, lineno, "record is not an object")
        recorded_hmac = parsed.get("hmac")
        if parsed.get("prev_hash") != prev:
            return ChainReport(count, False, lineno, "prev_hash mismatch")
        # Recompute over the line bytes minus the trailing hmac field.
        # Both writers serialize compactly with hmac as the final key, so
        # stripping the suffix reproduces the signed serialization exactly.
        marker = f',"hmac":{json.dumps(recorded_hmac)}'
        if not raw.endswith(marker + "}"):
            return ChainReport(count, False, lineno, "hmac field not in canonical position")
        serialized = raw[: -len(marker + "}")] + "}"
        if _compute_hmac(key, prev, serialized) != recorded_hmac:
            return ChainReport(count, False, lineno, "hmac mismatch")
        prev = str(recorded_hmac)
    return ChainReport(records=count, valid=True)
