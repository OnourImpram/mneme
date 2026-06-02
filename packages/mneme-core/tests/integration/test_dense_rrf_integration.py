"""Integration tests proving the RRF pipeline fuses FTS5 and a mock dense backend.

Covers:
- test_rrf_fuses_fts5_and_dense: mock dense hit survives RRF and appears in results.
- test_telemetry_captures_both_backends: telemetry JSONL records both 'fts5' and 'dense'.
- test_planner_does_not_activate_dense_when_unavailable: planner gates dense when not
  present in available_backends.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest

from mneme_core.fts5.indexer import IndexerConfig, ensure_schema, index_vault
from mneme_core.fts5.locale.tr import normalize_tr, normalize_tr_for_fts
from mneme_core.retrieval.planner import RetrievalConfig as PlannerConfig
from mneme_core.retrieval.planner import plan_retrieval
from mneme_core.retrieval.rrf import Hit, RetrievalConfig, retrieve
from mneme_core.vault.config import VaultConfig


@pytest.fixture
def indexed_db_with_vault(tmp_path: Path) -> Iterator[tuple[Path, VaultConfig]]:
    """Create a 3-doc vault, index it, and yield (db_path, vault_config).

    vault_config is a real VaultConfig whose telemetry_dir is rooted at
    tmp_path/.mneme/telemetry so telemetry JSONL files land in tmp_path.
    """
    vault_root = tmp_path / "vault"
    vault_root.mkdir()

    (vault_root / "a.md").write_text(
        "---\nid: a\ntype: session\ntags: memory\n---\n"
        "# Vault Native Memory\n"
        "Markdown is ground truth. Hybrid retrieval beats single backend.\n",
        encoding="utf-8",
    )
    (vault_root / "b.md").write_text(
        "---\nid: b\ntype: topic\ntags: retrieval\n---\n"
        "# Reciprocal Rank Fusion\n"
        "RRF at k equal sixty fuses ranked lists from multiple backends.\n",
        encoding="utf-8",
    )
    (vault_root / "c.md").write_text(
        "---\nid: c\ntype: reference\ntags: privacy\n---\n"
        "# Privacy Redaction\n"
        "Sensitive content is stripped at staging. Audit log records hashes.\n",
        encoding="utf-8",
    )

    db_path = tmp_path / "fts.db"
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

    # VaultConfig.from_path uses tmp_path as root; telemetry_dir resolves to
    # tmp_path/.mneme/telemetry which is writeable in the tmp fixture.
    vault_config = VaultConfig.from_path(tmp_path)

    yield db_path, vault_config


def _mock_dense(query: str, limit: int) -> list[Hit]:  # noqa: ARG001
    """Always return a single synthetic dense hit unrelated to the FTS5 index."""
    return [
        Hit(
            id=999,
            path="external.md",
            title="External",
            score=-0.5,
            source="dense",
        )
    ]


class TestRrfFusesFts5AndDense:
    def test_rrf_fuses_fts5_and_dense(
        self, indexed_db_with_vault: tuple[Path, VaultConfig]
    ) -> None:
        """Mock dense hit must survive RRF and appear in the final ranked list."""
        db_path, _ = indexed_db_with_vault

        config = RetrievalConfig(
            fts5_db=db_path,
            normalize=normalize_tr,
            min_query_length=3,
            top_n_final=10,
        )
        result = retrieve(
            "vault native memory",
            config,
            dense_backend=_mock_dense,
        )

        assert len(result) >= 1, "Expected at least one hit from the fused pipeline"
        # The external.md hit from mock dense OR at least one hit tagged with
        # 'dense' in its sources must appear.
        has_dense = any(
            h.path == "external.md" or "dense" in h.sources for h in result
        )
        assert has_dense, (
            f"Expected a dense hit in results; got paths={[h.path for h in result]}, "
            f"sources={[h.sources for h in result]}"
        )


class TestTelemetryCapturesBothBackends:
    def test_telemetry_captures_both_backends(
        self, indexed_db_with_vault: tuple[Path, VaultConfig]
    ) -> None:
        """The telemetry JSONL record must list both 'fts5' and 'dense' backends."""
        db_path, vault_config = indexed_db_with_vault

        config = RetrievalConfig(
            fts5_db=db_path,
            normalize=normalize_tr,
            min_query_length=3,
            top_n_final=10,
        )
        retrieve(
            "vault native memory",
            config,
            dense_backend=_mock_dense,
            vault=vault_config,
        )

        # Find the daily retrieval JSONL written by emit_retrieval_event.
        telemetry_dir = vault_config.telemetry_dir
        jsonl_files = list(telemetry_dir.glob("retrieval-*.jsonl"))
        assert jsonl_files, (
            f"No retrieval-*.jsonl found in {telemetry_dir}; "
            "telemetry was not emitted"
        )

        records = []
        for jsonl_path in jsonl_files:
            for line in jsonl_path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line:
                    records.append(json.loads(line))

        assert records, "Telemetry JSONL is empty after retrieve() call"

        # The most recent record (last line) is from our call.
        record = records[-1]
        assert "backends" in record, f"Record missing 'backends' key: {record}"
        assert "fts5" in record["backends"], (
            f"'fts5' not in record['backends']: {record['backends']}"
        )
        assert "dense" in record["backends"], (
            f"'dense' not in record['backends']: {record['backends']}"
        )

        assert "per_backend_hits" in record, (
            f"Record missing 'per_backend_hits': {record}"
        )
        assert record["per_backend_hits"].get("dense") == 1, (
            f"Expected per_backend_hits['dense']==1, got {record['per_backend_hits']}"
        )


class TestPlannerDoesNotActivateDenseWhenUnavailable:
    def test_planner_does_not_activate_dense_when_unavailable(self) -> None:
        """Planner must not include 'dense' when it is absent from available_backends."""
        planner_config = PlannerConfig(
            min_query_length=3,
            dense_enabled=True,  # dense is enabled in config...
        )
        # ...but not wired at runtime.
        result = plan_retrieval(
            "vault native memory",
            planner_config,
            available_backends=frozenset({"fts5"}),
        )
        assert "dense" not in result, (
            f"Planner should not activate dense when unavailable; got {result}"
        )
        assert "fts5" in result, f"Planner must always include fts5; got {result}"
