"""Cross-language tamper-evident HMAC audit chain.

Python and TypeScript writers share the same key, daily JSONL file, HMAC
rule, O_EXCL lock file, explicit daily ``sequence``, and keyed head seal.
Legacy records without a sequence remain valid, with their position in the
file acting as the implicit sequence.

Each successful Python or TypeScript append writes a keyed daily seal
containing the record count and head HMAC. The seal detects deletion of a
whole sealed file and truncation below the latest cross-language head.

No LLM and no network are used.
"""

from __future__ import annotations

import contextlib
import errno
import hmac as _hmac
import json
import os
import secrets
import sys
import time
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from typing import Any

from .vault.atomic_write import atomic_write_text

KEY_BYTES = 32
ZERO_HASH = "0" * 64

KEY_FILENAME = "audit-hmac.key"
AUDIT_DIR_NAME = "audit"
SEAL_VERSION = 1

_LOCK_TIMEOUT_S = 5.0
_LOCK_POLL_S = 0.01
_LOCK_STALE_S = 10.0
_KEY_READ_TIMEOUT_S = 0.5
_SEAL_DOMAIN = b"mneme-audit-seal-v1\x00"
_RESERVED_RECORD_FIELDS = frozenset(
    {"timestamp_iso", "sequence", "prev_hash", "hmac"}
)


def _read_key(key_path: Path) -> bytes:
    deadline = time.monotonic() + _KEY_READ_TIMEOUT_S
    while True:
        data = key_path.read_bytes()
        if len(data) == KEY_BYTES:
            return data
        if time.monotonic() >= deadline:
            raise ValueError(
                f"{KEY_FILENAME} is {len(data)} bytes; expected {KEY_BYTES}. "
                f"Delete {key_path} to regenerate."
            )
        time.sleep(_LOCK_POLL_S)


def _load_or_create_key(state_dir: Path) -> bytes:
    """Load the shared key, creating it exclusively with mode 0o600."""
    key_path = state_dir / KEY_FILENAME
    if key_path.is_file():
        return _read_key(key_path)

    state_dir.mkdir(parents=True, exist_ok=True)
    key = secrets.token_bytes(KEY_BYTES)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0)
    try:
        fd = os.open(key_path, flags, 0o600)
    except OSError as exc:
        if exc.errno not in {errno.EEXIST, errno.EACCES, errno.EPERM}:
            raise
        return _read_key(key_path)

    with os.fdopen(fd, "wb") as fp:
        fp.write(key)
        fp.flush()
        os.fsync(fp.fileno())
    return key


@contextlib.contextmanager
def _audit_lock(lock_path: Path) -> Iterator[None]:
    """Acquire the O_EXCL lock protocol used by the TypeScript writer."""
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    deadline = time.monotonic() + _LOCK_TIMEOUT_S
    token = f"{os.getpid()}:{secrets.token_hex(8)}".encode("ascii")

    while True:
        try:
            fd = os.open(lock_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except OSError as exc:
            if exc.errno not in {errno.EEXIST, errno.EACCES, errno.EPERM}:
                raise
            if not lock_path.exists():
                continue
            try:
                if time.time() - lock_path.stat().st_mtime > _LOCK_STALE_S:
                    lock_path.unlink(missing_ok=True)
                    continue
            except FileNotFoundError:
                continue
            except OSError:
                pass
            if time.monotonic() >= deadline:
                raise TimeoutError(
                    f"Could not acquire audit lock at {lock_path} "
                    f"within {_LOCK_TIMEOUT_S}s"
                ) from exc
            time.sleep(_LOCK_POLL_S)
            continue

        try:
            os.write(fd, token)
        except BaseException:
            os.close(fd)
            with contextlib.suppress(OSError):
                lock_path.unlink()
            raise
        os.close(fd)
        break

    try:
        yield
    finally:
        try:
            if lock_path.read_bytes() == token:
                lock_path.unlink()
        except (FileNotFoundError, OSError):
            pass


def _compute_hmac(key: bytes, prev_hash: str, serialized: str) -> str:
    payload = (prev_hash + serialized).encode("utf-8")
    return _hmac.new(key, payload, sha256).hexdigest()


def _compute_seal_hmac(key: bytes, serialized: str) -> str:
    payload = _SEAL_DOMAIN + serialized.encode("utf-8")
    return _hmac.new(key, payload, sha256).hexdigest()


def _serialize(record: dict[str, Any]) -> str:
    """Serialize compactly in insertion order, matching JSON.stringify."""
    return json.dumps(record, separators=(",", ":"), ensure_ascii=False)


@dataclass(frozen=True)
class ChainReport:
    """Result of verifying one daily chain and its latest Python seal."""

    records: int
    valid: bool
    first_break_line: int | None = None
    detail: str = ""


def _scan_chain(
    jsonl_path: Path,
    key: bytes,
) -> tuple[ChainReport, tuple[str, ...], bool]:
    if not jsonl_path.is_file():
        return ChainReport(records=0, valid=True, detail="no chain file"), (), False
    try:
        lines = jsonl_path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        return ChainReport(0, False, detail=f"chain unreadable: {exc}"), (), False

    prev = ZERO_HASH
    count = 0
    heads: list[str] = []
    explicit_sequence_seen = False
    for lineno, raw_line in enumerate(lines, start=1):
        raw = raw_line.strip()
        if not raw:
            continue
        count += 1
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            return (
                ChainReport(count, False, lineno, "unparseable record"),
                tuple(heads),
                explicit_sequence_seen,
            )
        if not isinstance(parsed, dict):
            return (
                ChainReport(count, False, lineno, "record is not an object"),
                tuple(heads),
                explicit_sequence_seen,
            )

        sequence = parsed.get("sequence")
        if sequence is not None:
            explicit_sequence_seen = True
            if type(sequence) is not int or sequence != count:
                return (
                    ChainReport(count, False, lineno, "sequence mismatch"),
                    tuple(heads),
                    explicit_sequence_seen,
                )

        recorded_hmac = parsed.get("hmac")
        if (
            not isinstance(recorded_hmac, str)
            or len(recorded_hmac) != 64
            or any(ch not in "0123456789abcdef" for ch in recorded_hmac)
        ):
            return (
                ChainReport(count, False, lineno, "invalid hmac"),
                tuple(heads),
                explicit_sequence_seen,
            )
        if parsed.get("prev_hash") != prev:
            return (
                ChainReport(count, False, lineno, "prev_hash mismatch"),
                tuple(heads),
                explicit_sequence_seen,
            )

        marker = f',"hmac":{json.dumps(recorded_hmac)}'
        if not raw.endswith(marker + "}"):
            return (
                ChainReport(
                    count,
                    False,
                    lineno,
                    "hmac field not in canonical position",
                ),
                tuple(heads),
                explicit_sequence_seen,
            )
        serialized = raw[: -len(marker + "}")] + "}"
        expected = _compute_hmac(key, prev, serialized)
        if not _hmac.compare_digest(expected, recorded_hmac):
            return (
                ChainReport(count, False, lineno, "hmac mismatch"),
                tuple(heads),
                explicit_sequence_seen,
            )
        prev = recorded_hmac
        heads.append(recorded_hmac)

    return ChainReport(records=count, valid=True), tuple(heads), explicit_sequence_seen


def _seal_path(audit_dir: Path, day: str) -> Path:
    return audit_dir / f"{day}.seal.json"


def _write_seal(
    seal_path: Path,
    *,
    vault_root: Path,
    day: str,
    sequence: int,
    head_hmac: str,
    key: bytes,
) -> None:
    body: dict[str, Any] = {
        "version": SEAL_VERSION,
        "day": day,
        "sequence": sequence,
        "head_hmac": head_hmac,
        "sealed_at": datetime.now(UTC).isoformat(),
    }
    seal_hmac = _compute_seal_hmac(key, _serialize(body))
    atomic_write_text(
        seal_path,
        _serialize({**body, "seal_hmac": seal_hmac}) + "\n",
        vault_root=vault_root,
    )


def _restore_snapshot(
    path: Path,
    *,
    vault_root: Path,
    existed: bool,
    content: str,
) -> None:
    if existed:
        atomic_write_text(path, content, vault_root=vault_root)
    else:
        path.unlink(missing_ok=True)


def _verify_seal(
    seal_path: Path,
    *,
    day: str,
    key: bytes,
    chain_heads: tuple[str, ...],
    explicit_sequence_seen: bool,
) -> tuple[bool, str]:
    if not seal_path.is_file():
        if explicit_sequence_seen:
            return False, "seal missing for Python-sequenced audit chain"
        if chain_heads:
            return True, "chain valid but no Python seal is present"
        return True, "no chain file"
    try:
        parsed = json.loads(seal_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return False, f"seal unreadable: {exc}"
    if not isinstance(parsed, dict):
        return False, "seal is not an object"

    version = parsed.get("version")
    seal_day = parsed.get("day")
    sequence = parsed.get("sequence")
    head_hmac = parsed.get("head_hmac")
    sealed_at = parsed.get("sealed_at")
    seal_hmac = parsed.get("seal_hmac")
    if version != SEAL_VERSION or seal_day != day:
        return False, "seal metadata mismatch"
    if type(sequence) is not int or sequence < 1:
        return False, "seal sequence is invalid"
    if not isinstance(head_hmac, str) or len(head_hmac) != 64:
        return False, "seal head is invalid"
    if not isinstance(sealed_at, str) or not isinstance(seal_hmac, str):
        return False, "seal fields are invalid"

    body: dict[str, Any] = {
        "version": version,
        "day": seal_day,
        "sequence": sequence,
        "head_hmac": head_hmac,
        "sealed_at": sealed_at,
    }
    expected = _compute_seal_hmac(key, _serialize(body))
    if not _hmac.compare_digest(expected, seal_hmac):
        return False, "seal hmac mismatch"
    if sequence > len(chain_heads):
        return False, (
            f"tail truncation detected: seal requires {sequence} records, "
            f"chain has {len(chain_heads)}"
        )
    if chain_heads[sequence - 1] != head_hmac:
        return False, "sealed head mismatch"
    if sequence < len(chain_heads):
        suffix = len(chain_heads) - sequence
        return True, f"chain valid with {suffix} unsealed cross-language record(s)"
    return True, "chain and seal valid"


def append_chain_record(state_dir: Path, record: dict[str, Any]) -> bool:
    """Append one record and advance the keyed daily head seal.

    The low-level API remains non-throwing for compatibility. Callers that
    require durable accountability must treat ``False`` as a hard failure.
    """
    try:
        reserved = _RESERVED_RECORD_FIELDS.intersection(record)
        if reserved:
            joined = ", ".join(sorted(reserved))
            raise ValueError(f"audit payload contains reserved fields: {joined}")

        audit_dir = state_dir / AUDIT_DIR_NAME
        audit_dir.mkdir(parents=True, exist_ok=True)
        key = _load_or_create_key(state_dir)
        day = datetime.now(UTC).strftime("%Y-%m-%d")
        jsonl_path = audit_dir / f"{day}.jsonl"
        seal_path = _seal_path(audit_dir, day)
        lock_path = audit_dir / f"{day}.lock"

        with _audit_lock(lock_path):
            report, heads, explicit_sequence_seen = _scan_chain(jsonl_path, key)
            if not report.valid:
                raise ValueError(f"existing audit chain is invalid: {report.detail}")
            seal_valid, seal_detail = _verify_seal(
                seal_path,
                day=day,
                key=key,
                chain_heads=heads,
                explicit_sequence_seen=explicit_sequence_seen,
            )
            if not seal_valid:
                raise ValueError(f"existing audit seal is invalid: {seal_detail}")

            sequence = report.records + 1
            prev_hash = heads[-1] if heads else ZERO_HASH
            without_hmac: dict[str, Any] = {
                "timestamp_iso": datetime.now(UTC).isoformat(),
                "sequence": sequence,
                **record,
                "prev_hash": prev_hash,
            }
            serialized = _serialize(without_hmac)
            digest = _compute_hmac(key, prev_hash, serialized)
            full = {**without_hmac, "hmac": digest}
            chain_existed = jsonl_path.is_file()
            existing = jsonl_path.read_text(encoding="utf-8") if chain_existed else ""
            seal_existed = seal_path.is_file()
            existing_seal = seal_path.read_text(encoding="utf-8") if seal_existed else ""
            separator = "" if not existing or existing.endswith(("\n", "\r")) else "\n"
            try:
                atomic_write_text(
                    jsonl_path,
                    existing + separator + _serialize(full) + "\n",
                    vault_root=state_dir,
                )
                _write_seal(
                    seal_path,
                    vault_root=state_dir,
                    day=day,
                    sequence=sequence,
                    head_hmac=digest,
                    key=key,
                )
            except Exception as append_exc:
                try:
                    _restore_snapshot(
                        jsonl_path,
                        vault_root=state_dir,
                        existed=chain_existed,
                        content=existing,
                    )
                    _restore_snapshot(
                        seal_path,
                        vault_root=state_dir,
                        existed=seal_existed,
                        content=existing_seal,
                    )
                except Exception as restore_exc:
                    raise RuntimeError(
                        "audit append failed and snapshot restoration failed: "
                        f"{restore_exc}"
                    ) from append_exc
                raise
        return True
    except Exception as exc:  # noqa: BLE001 - compatibility boundary
        sys.stderr.write(f"[mneme audit] chain append failed: {exc}\n")
        return False


def verify_chain(state_dir: Path, day: str) -> ChainReport:
    """Verify a daily chain, explicit sequences, and the latest keyed seal."""
    audit_dir = state_dir / AUDIT_DIR_NAME
    jsonl_path = audit_dir / f"{day}.jsonl"
    seal_path = _seal_path(audit_dir, day)
    if not jsonl_path.is_file() and not seal_path.is_file():
        return ChainReport(records=0, valid=True, detail="no chain file")
    lock_path = audit_dir / f"{day}.lock"
    try:
        with _audit_lock(lock_path):
            key = _load_or_create_key(state_dir)
            report, heads, explicit_sequence_seen = _scan_chain(jsonl_path, key)
            if not report.valid:
                return report
            seal_valid, detail = _verify_seal(
                seal_path,
                day=day,
                key=key,
                chain_heads=heads,
                explicit_sequence_seen=explicit_sequence_seen,
            )
            return ChainReport(
                records=report.records,
                valid=seal_valid,
                first_break_line=None,
                detail=detail,
            )
    except (OSError, TimeoutError, ValueError) as exc:
        return ChainReport(records=0, valid=False, detail=f"verification unavailable: {exc}")
