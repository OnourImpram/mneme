"""Integration tests for the parity-check harness using in-memory adapters."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tools.parity.adapter import (
    ParityAdapter,
    ParityAdapterStatus,
    ParityHit,
)
from tools.parity.harness import (
    load_queries_from_json,
    run_parity_check,
)


class InMemoryAdapter:
    """Returns canned responses keyed by query text. For tests only."""

    def __init__(self, name: str, responses: dict[str, list[ParityHit]]) -> None:
        self._name = name
        self._responses = responses
        self._available = True
        self._reason = ""

    def set_unavailable(self, reason: str) -> None:
        self._available = False
        self._reason = reason

    def status(self) -> ParityAdapterStatus:
        return ParityAdapterStatus(
            name=self._name, available=self._available, reason=self._reason
        )

    def search(self, query: str, top_k: int = 10) -> list[ParityHit]:
        return self._responses.get(query, [])[:top_k]


def _hit(doc_id: str, score: float = 1.0) -> ParityHit:
    return ParityHit(doc_id=doc_id, score=score)


class TestHarnessGate:
    def test_perfect_agreement_passes_gate(self) -> None:
        queries = [{"id": f"q{i}", "text": f"query-{i}"} for i in range(5)]
        same: dict[str, list[ParityHit]] = {
            f"query-{i}": [_hit(f"d{j}") for j in range(5)]
            for i in range(5)
        }
        mneme: ParityAdapter = InMemoryAdapter("mneme", same)
        gt: ParityAdapter = InMemoryAdapter("gt", same)
        report = run_parity_check(
            mneme=mneme, ground_truth=gt, queries=queries
        )
        assert report["comparable"] is True
        summary = report["summary"]
        assert summary["gate_passed"] is True
        assert summary["mean_top_5_jaccard"] == 1.0

    def test_total_disagreement_fails_gate(self) -> None:
        queries = [{"id": f"q{i}", "text": f"query-{i}"} for i in range(5)]
        mneme_resp = {f"query-{i}": [_hit(f"a{i}")] * 5 for i in range(5)}
        gt_resp = {f"query-{i}": [_hit(f"b{i}")] * 5 for i in range(5)}
        report = run_parity_check(
            mneme=InMemoryAdapter("mneme", mneme_resp),
            ground_truth=InMemoryAdapter("gt", gt_resp),
            queries=queries,
        )
        assert report["summary"]["gate_passed"] is False

    def test_mneme_unavailable_returns_not_comparable(self) -> None:
        mneme = InMemoryAdapter("mneme", {})
        mneme.set_unavailable("vault missing")
        gt = InMemoryAdapter("gt", {})
        report = run_parity_check(
            mneme=mneme,
            ground_truth=gt,
            queries=[{"id": "q1", "text": "anything"}],
        )
        assert report["comparable"] is False
        assert "vault missing" in report["reason"]

    def test_ground_truth_unavailable_returns_not_comparable(self) -> None:
        gt = InMemoryAdapter("gt", {})
        gt.set_unavailable("adapter not configured")
        report = run_parity_check(
            mneme=InMemoryAdapter("mneme", {}),
            ground_truth=gt,
            queries=[{"id": "q1", "text": "anything"}],
        )
        assert report["comparable"] is False
        assert "adapter not configured" in report["reason"]

    def test_skips_queries_with_missing_text(self) -> None:
        queries = [
            {"id": "q1", "text": "valid"},
            {"id": "q2", "text": ""},          # skipped
            {"id": "", "text": "no qid"},     # skipped
        ]
        responses = {"valid": [_hit("d1")]}
        report = run_parity_check(
            mneme=InMemoryAdapter("mneme", responses),
            ground_truth=InMemoryAdapter("gt", responses),
            queries=queries,
        )
        assert report["queries_evaluated"] == 1


class TestLoadQueries:
    def test_loads_well_formed(self, tmp_path: Path) -> None:
        path = tmp_path / "queries.json"
        path.write_text(
            json.dumps(
                [
                    {"id": "q1", "text": "first"},
                    {"id": "q2", "text": "second"},
                ]
            ),
            encoding="utf-8",
        )
        out = load_queries_from_json(path)
        assert len(out) == 2
        assert out[0] == {"id": "q1", "text": "first"}

    def test_skips_rows_without_text(self, tmp_path: Path) -> None:
        path = tmp_path / "queries.json"
        path.write_text(
            json.dumps([
                {"id": "q1", "text": "ok"},
                {"id": "q2"},
                {"id": "q3", "text": ""},
            ]),
            encoding="utf-8",
        )
        out = load_queries_from_json(path)
        assert len(out) == 1

    def test_accepts_qid_alias(self, tmp_path: Path) -> None:
        path = tmp_path / "queries.json"
        path.write_text(
            json.dumps([{"qid": "alt-id", "text": "alias"}]), encoding="utf-8"
        )
        out = load_queries_from_json(path)
        assert out[0]["id"] == "alt-id"

    def test_assigns_default_qid_when_missing(self, tmp_path: Path) -> None:
        path = tmp_path / "queries.json"
        path.write_text(
            json.dumps([{"text": "no-id"}]), encoding="utf-8"
        )
        out = load_queries_from_json(path)
        assert out[0]["id"].startswith("q")

    def test_rejects_non_list_payload(self, tmp_path: Path) -> None:
        path = tmp_path / "queries.json"
        path.write_text(json.dumps({"not": "a list"}), encoding="utf-8")
        with pytest.raises(ValueError):
            load_queries_from_json(path)
