"""Unit tests for mneme_core.retrieval.telemetry.

Covers:
- RetrievalEvent dataclass roundtrip and per-vault HMAC query identifiers
- emit_retrieval_event writes correct JSONL to telemetry_dir
- emit_retrieval_event never raises (wraps all errors)
- retrieve() wires telemetry when vault kwarg is supplied
"""

from __future__ import annotations

import hashlib
import hmac
import json
import sqlite3
from pathlib import Path

import pytest

from mneme_core.fts5.indexer import IndexerConfig, ensure_schema, index_vault
from mneme_core.retrieval.rrf import Hit, RetrievalConfig, retrieve
from mneme_core.retrieval.telemetry import RetrievalEvent, emit_retrieval_event
from mneme_core.vault.config import VaultConfig


def _make_vault(tmp_path: Path) -> VaultConfig:
    vault_root = tmp_path / "vault"
    vault_root.mkdir()
    return VaultConfig.from_path(vault_root)


def _today_iso() -> str:
    from datetime import UTC, datetime

    return datetime.now(UTC).strftime("%Y-%m-%d")


# --- RetrievalEvent dataclass ---


def test_retrieval_event_fields() -> None:
    """RetrievalEvent carries all required fields."""
    event = RetrievalEvent(
        query_hash="abc123",
        backends=["fts5"],
        per_backend_hits={"fts5": 3},
        per_backend_latency_ms={"fts5": 12.5},
        per_backend_status={
            "fts5": {
                "attempted": True,
                "succeeded": True,
                "failed": False,
                "contributed": True,
            }
        },
        fused_count=3,
        top1_source="notes/foo.md",
        timestamp_iso="2026-06-01T00:00:00+00:00",
    )
    assert event.query_hash == "abc123"
    assert event.backends == ["fts5"]
    assert event.per_backend_hits == {"fts5": 3}
    assert event.per_backend_latency_ms == {"fts5": 12.5}
    assert event.per_backend_status["fts5"]["contributed"] is True
    assert event.fused_count == 3
    assert event.top1_source == "notes/foo.md"
    assert event.timestamp_iso == "2026-06-01T00:00:00+00:00"


def test_retrieval_event_top1_source_nullable() -> None:
    """top1_source may be None (no hits)."""
    event = RetrievalEvent(
        query_hash="x",
        backends=[],
        per_backend_hits={},
        per_backend_latency_ms={},
        per_backend_status={},
        fused_count=0,
        top1_source=None,
        timestamp_iso="2026-06-01T00:00:00+00:00",
    )
    assert event.top1_source is None


def _expected_query_hmac(vault: VaultConfig, value: str) -> str:
    key = (vault.state_dir / "telemetry-hmac.key").read_bytes()
    return hmac.new(key, value.encode("utf-8"), "sha256").hexdigest()


def test_query_hash_is_per_vault_hmac_not_raw(tmp_path: Path) -> None:
    """The sink stores a vault-keyed identifier, never raw query text."""
    vault = _make_vault(tmp_path)
    raw_query = "cognitive load research"
    event = RetrievalEvent(
        query_hash=raw_query,
        backends=["fts5"],
        per_backend_hits={"fts5": 2},
        per_backend_latency_ms={"fts5": 8.0},
        per_backend_status={},
        fused_count=2,
        top1_source="notes/a.md",
        timestamp_iso=_today_iso() + "T00:00:00+00:00",
    )
    emit_retrieval_event(vault, event)

    today = _today_iso()
    jsonl_path = vault.telemetry_dir / f"retrieval-{today}.jsonl"
    assert jsonl_path.exists(), f"JSONL not created: {jsonl_path}"
    lines = jsonl_path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    record = json.loads(lines[0])
    assert record["query_hash"] == _expected_query_hmac(vault, raw_query)
    assert raw_query not in json.dumps(record), "raw query text must not appear in record"


def test_same_query_differs_across_vaults(tmp_path: Path) -> None:
    first_root = tmp_path / "first"
    second_root = tmp_path / "second"
    first_root.mkdir()
    second_root.mkdir()
    first = _make_vault(first_root)
    second = _make_vault(second_root)
    query = "bipolar medication"
    event = RetrievalEvent(
        query_hash=query,
        backends=[],
        per_backend_hits={},
        per_backend_latency_ms={},
        per_backend_status={},
        fused_count=0,
        top1_source=None,
        timestamp_iso="2026-06-01T00:00:00+00:00",
    )

    emit_retrieval_event(first, event)
    emit_retrieval_event(second, event)

    assert _expected_query_hmac(first, query) != _expected_query_hmac(second, query)


def test_sink_hashes_raw_query_and_redacts_string_metadata(tmp_path: Path) -> None:
    vault = _make_vault(tmp_path)
    raw_query = "raw patient query that must not persist"
    event = RetrievalEvent(
        query_hash=raw_query,
        backends=["fts5", "<private>BACKEND_SECRET</private>"],
        per_backend_hits={"<private>HIT_SECRET</private>": 1},
        per_backend_latency_ms={"fts5": 2.0},
        per_backend_status={
            "<private>STATUS_SECRET</private>": {
                "attempted": True,
                "succeeded": True,
                "failed": False,
                "contributed": True,
            }
        },
        fused_count=1,
        top1_source="notes/<private>PATH_SECRET</private>.md",
        timestamp_iso="2026-06-01T00:00:00+00:00",
    )

    emit_retrieval_event(vault, event)

    path = vault.telemetry_dir / f"retrieval-{_today_iso()}.jsonl"
    raw = path.read_text(encoding="utf-8")
    record = json.loads(raw)
    assert raw_query not in raw
    assert record["query_hash"] == _expected_query_hmac(vault, raw_query)
    for secret in ("BACKEND_SECRET", "HIT_SECRET", "STATUS_SECRET", "PATH_SECRET"):
        assert secret not in raw


# --- emit_retrieval_event writes JSONL ---


def test_emit_writes_jsonl(tmp_path: Path) -> None:
    """emit_retrieval_event creates retrieval-YYYY-MM-DD.jsonl in telemetry_dir."""
    vault = _make_vault(tmp_path)
    today = _today_iso()
    event = RetrievalEvent(
        query_hash=hashlib.sha256(b"test query").hexdigest(),
        backends=["fts5", "dense"],
        per_backend_hits={"fts5": 5, "dense": 3},
        per_backend_latency_ms={"fts5": 10.0, "dense": 20.0},
        per_backend_status={
            "fts5": {
                "attempted": True,
                "succeeded": True,
                "failed": False,
                "contributed": True,
            },
            "dense": {
                "attempted": True,
                "succeeded": True,
                "failed": False,
                "contributed": True,
            },
        },
        fused_count=6,
        top1_source="notes/b.md",
        timestamp_iso=today + "T12:00:00+00:00",
    )
    emit_retrieval_event(vault, event)

    jsonl_path = vault.telemetry_dir / f"retrieval-{today}.jsonl"
    assert jsonl_path.exists()
    record = json.loads(jsonl_path.read_text(encoding="utf-8").strip())
    assert record["backends"] == ["fts5", "dense"]
    assert record["per_backend_hits"] == {"fts5": 5, "dense": 3}
    assert record["per_backend_status"]["dense"]["succeeded"] is True
    assert record["fused_count"] == 6
    assert record["top1_source"] == "notes/b.md"


def test_emit_appends_multiple_events(tmp_path: Path) -> None:
    """emit_retrieval_event appends; second call produces two lines."""
    vault = _make_vault(tmp_path)
    today = _today_iso()

    for i in range(2):
        event = RetrievalEvent(
            query_hash=hashlib.sha256(f"q{i}".encode()).hexdigest(),
            backends=["fts5"],
            per_backend_hits={"fts5": i},
            per_backend_latency_ms={"fts5": float(i)},
            per_backend_status={},
            fused_count=i,
            top1_source=None,
            timestamp_iso=today + "T00:00:00+00:00",
        )
        emit_retrieval_event(vault, event)

    jsonl_path = vault.telemetry_dir / f"retrieval-{today}.jsonl"
    lines = [ln for ln in jsonl_path.read_text(encoding="utf-8").splitlines() if ln.strip()]
    assert len(lines) == 2


def test_emit_never_raises_on_bad_path(tmp_path: Path) -> None:
    """emit_retrieval_event must not raise even when the vault is unwritable."""
    # Use a vault root pointing at a file (not a dir) to force an OSError.
    fake_root = tmp_path / "not_a_dir"
    fake_root.write_text("x", encoding="utf-8")
    vault = VaultConfig(root=fake_root)

    event = RetrievalEvent(
        query_hash="abc",
        backends=[],
        per_backend_hits={},
        per_backend_latency_ms={},
        per_backend_status={},
        fused_count=0,
        top1_source=None,
        timestamp_iso="2026-06-01T00:00:00+00:00",
    )
    # Must not raise
    emit_retrieval_event(vault, event)


def test_emit_jsonl_is_valid_json(tmp_path: Path) -> None:
    """Each line in the JSONL file is parseable JSON."""
    vault = _make_vault(tmp_path)
    today = _today_iso()
    event = RetrievalEvent(
        query_hash=hashlib.sha256(b"q").hexdigest(),
        backends=["fts5"],
        per_backend_hits={"fts5": 1},
        per_backend_latency_ms={"fts5": 5.5},
        per_backend_status={},
        fused_count=1,
        top1_source="x.md",
        timestamp_iso=today + "T00:00:00+00:00",
    )
    emit_retrieval_event(vault, event)
    jsonl_path = vault.telemetry_dir / f"retrieval-{today}.jsonl"
    for line in jsonl_path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            json.loads(line)  # must not raise


# --- retrieve() telemetry wiring ---


@pytest.fixture()
def _indexed_vault(tmp_path: Path) -> tuple[VaultConfig, Path]:
    """Build a minimal vault + FTS5 DB and return (VaultConfig, db_path)."""
    from mneme_core.fts5.locale.tr import normalize_tr, normalize_tr_for_fts

    vault_root = tmp_path / "vault"
    vault_root.mkdir()
    (vault_root / "note.md").write_text(
        "---\nid: note1\ntype: note\ntags: test\n---\n"
        "# Hybrid Retrieval\n"
        "Reciprocal rank fusion combines multiple backends.\n",
        encoding="utf-8",
    )
    # Place the FTS5 DB in .mneme/ so VaultConfig.fts5_db matches.
    state_dir = vault_root / ".mneme"
    state_dir.mkdir()
    db_path = state_dir / "fts5.sqlite"
    conn = sqlite3.connect(db_path)
    ensure_schema(conn)
    cfg = IndexerConfig(
        vault_root=vault_root,
        db_path=db_path,
        normalize=normalize_tr,
        normalize_for_fts=normalize_tr_for_fts,
    )
    index_vault(conn, cfg)
    conn.close()
    vault = VaultConfig.from_path(vault_root)
    return vault, db_path


def test_retrieve_wires_telemetry(_indexed_vault: tuple[VaultConfig, Path]) -> None:
    """retrieve() with vault kwarg writes a JSONL record with correct keys."""
    vault, db_path = _indexed_vault
    today = _today_iso()

    cfg = RetrievalConfig(
        fts5_db=db_path,
        min_query_length=3,
        top_n_final=5,
    )
    retrieve("hybrid retrieval fusion", cfg, vault=vault)

    jsonl_path = vault.telemetry_dir / f"retrieval-{today}.jsonl"
    assert jsonl_path.exists(), f"telemetry JSONL not created at {jsonl_path}"

    lines = [ln for ln in jsonl_path.read_text(encoding="utf-8").splitlines() if ln.strip()]
    assert len(lines) >= 1, "expected at least one telemetry record"

    record = json.loads(lines[0])
    # Required keys per RetrievalEvent / spec
    for key in (
        "query_hash",
        "backends",
        "per_backend_hits",
        "per_backend_latency_ms",
        "per_backend_status",
        "fused_count",
        "timestamp_iso",
    ):
        assert key in record, f"missing key {key!r} in telemetry record"

    # query_hash must be a 64-char sha256 hex digest, not raw query text
    assert len(record["query_hash"]) == 64
    assert "hybrid" not in record["query_hash"]

    # fts5 backend must be present
    assert "fts5" in record["backends"]
    assert "fts5" in record["per_backend_hits"]
    assert "fts5" in record["per_backend_latency_ms"]
    assert record["per_backend_status"]["fts5"] == {
        "attempted": True,
        "succeeded": True,
        "failed": False,
        "contributed": True,
    }


def test_retrieve_distinguishes_empty_backend_from_failure(
    _indexed_vault: tuple[VaultConfig, Path],
) -> None:
    vault, db_path = _indexed_vault
    cfg = RetrievalConfig(fts5_db=db_path, min_query_length=3, top_n_final=5)

    def empty_dense(_query: str, _limit: int) -> list[Hit]:
        return []

    retrieve("hybrid retrieval fusion", cfg, dense_backend=empty_dense, vault=vault)
    jsonl_path = vault.telemetry_dir / f"retrieval-{_today_iso()}.jsonl"
    empty_record = json.loads(jsonl_path.read_text(encoding="utf-8").splitlines()[-1])
    assert empty_record["per_backend_status"]["dense"] == {
        "attempted": True,
        "succeeded": True,
        "failed": False,
        "contributed": False,
    }

    def failed_dense(_query: str, _limit: int) -> list[Hit]:
        raise RuntimeError("backend unavailable")

    retrieve("hybrid retrieval fusion", cfg, dense_backend=failed_dense, vault=vault)
    failed_record = json.loads(jsonl_path.read_text(encoding="utf-8").splitlines()[-1])
    assert failed_record["per_backend_status"]["dense"] == {
        "attempted": True,
        "succeeded": False,
        "failed": True,
        "contributed": False,
    }


def test_retrieve_no_vault_skips_telemetry(tmp_path: Path) -> None:
    """retrieve() with vault=None (default) must not create any telemetry file."""
    cfg = RetrievalConfig(
        fts5_db=tmp_path / "no.db",
        min_query_length=3,
    )
    # No vault kwarg — must not raise and must not create any file
    retrieve("hybrid retrieval fusion", cfg)
    assert not list(tmp_path.rglob("retrieval-*.jsonl"))
