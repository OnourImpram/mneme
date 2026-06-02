"""TDD tests for benchmarks/longmemeval/run.py and regression_guard.py.

Red phase: both run.py and regression_guard.py are absent — imports will fail.
Green phase: after implementation, all assertions pass.
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

import pytest

# Allow running from repo root without package install.
_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_REPO_ROOT / "packages" / "mneme-core" / "src"))

# ---------------------------------------------------------------------------
# Helpers shared across tests
# ---------------------------------------------------------------------------

_BENCH_DIR = Path(__file__).resolve().parent


def _load_baseline() -> dict:
    return json.loads((_BENCH_DIR / "baseline.json").read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# run.py tests
# ---------------------------------------------------------------------------


class TestRunModule:
    """run.py must be importable and expose the expected public API."""

    def test_module_importable(self) -> None:
        from benchmarks.longmemeval import run  # noqa: F401

    def test_run_returns_non_zero_recall(self) -> None:
        """run_benchmark() must return recall_at_1 > 0 on the synthetic fixture."""
        from benchmarks.longmemeval.run import run_benchmark

        result = run_benchmark()
        assert result["results"]["recall_at_1"] > 0.0, (
            f"recall_at_1 is {result['results']['recall_at_1']} — expected > 0"
        )

    def test_run_returns_non_zero_mrr(self) -> None:
        from benchmarks.longmemeval.run import run_benchmark

        result = run_benchmark()
        assert result["results"]["mrr_at_10"] > 0.0

    def test_abstention_accuracy_is_one(self) -> None:
        """The two abstention queries must return no results (FTS5 vocab mismatch)."""
        from benchmarks.longmemeval.run import run_benchmark

        result = run_benchmark()
        assert result["results"]["abstention_accuracy"] == 1.0

    def test_case_count_is_eight(self) -> None:
        from benchmarks.longmemeval.run import run_benchmark

        result = run_benchmark()
        assert result["results"]["case_count"] == 8

    def test_abstention_count_is_two(self) -> None:
        from benchmarks.longmemeval.run import run_benchmark

        result = run_benchmark()
        assert result["results"]["abstention_count"] == 2

    def test_result_shape(self) -> None:
        """All required metric keys must be present."""
        from benchmarks.longmemeval.run import run_benchmark

        result = run_benchmark()
        for key in (
            "recall_at_1",
            "recall_at_5",
            "recall_at_10",
            "mrr_at_10",
            "abstention_accuracy",
            "case_count",
            "abstention_count",
        ):
            assert key in result["results"], f"missing key: {key}"

    def test_recall_at_k_ordering(self) -> None:
        """recall_at_10 >= recall_at_5 >= recall_at_1 (monotone by k)."""
        from benchmarks.longmemeval.run import run_benchmark

        r = run_benchmark()["results"]
        assert r["recall_at_10"] >= r["recall_at_5"] >= r["recall_at_1"]

    def test_write_baseline_updates_file(self) -> None:
        """run_benchmark(write_baseline=True) must write actual numbers to baseline.json."""
        from benchmarks.longmemeval.run import run_benchmark

        with tempfile.TemporaryDirectory() as tmp:
            out_path = Path(tmp) / "baseline_test.json"
            run_benchmark(write_baseline=True, output_path=out_path)
            payload = json.loads(out_path.read_text(encoding="utf-8"))
            assert payload["results"]["recall_at_1"] > 0.0


# ---------------------------------------------------------------------------
# Metric helper tests (pure-function, no I/O)
# ---------------------------------------------------------------------------


class TestMetricHelpers:
    def test_recall_at_k_hit(self) -> None:
        from benchmarks.longmemeval.run import recall_at_k

        ranked = ["notes/a.md", "notes/b.md", "notes/c.md"]
        assert recall_at_k(ranked, {"notes/a.md"}, k=1) == 1.0

    def test_recall_at_k_miss(self) -> None:
        from benchmarks.longmemeval.run import recall_at_k

        ranked = ["notes/b.md", "notes/c.md"]
        assert recall_at_k(ranked, {"notes/a.md"}, k=1) == 0.0

    def test_recall_at_k_empty_gold(self) -> None:
        from benchmarks.longmemeval.run import recall_at_k

        assert recall_at_k(["notes/a.md"], set(), k=5) == 0.0

    def test_recall_at_k_stem_match(self) -> None:
        """Stem-level matching: 'notes/a.md' matches gold 'other/a.md'."""
        from benchmarks.longmemeval.run import recall_at_k

        ranked = ["tmp/vault/notes/a.md"]
        gold = {"notes/a.md"}
        assert recall_at_k(ranked, gold, k=1) == 1.0

    def test_mrr_at_10_first_rank(self) -> None:
        from benchmarks.longmemeval.run import mrr_at_10

        ranked = ["notes/a.md", "notes/b.md"]
        assert mrr_at_10(ranked, {"notes/a.md"}) == pytest.approx(1.0)

    def test_mrr_at_10_second_rank(self) -> None:
        from benchmarks.longmemeval.run import mrr_at_10

        ranked = ["notes/b.md", "notes/a.md"]
        assert mrr_at_10(ranked, {"notes/a.md"}) == pytest.approx(0.5)

    def test_mrr_at_10_miss(self) -> None:
        from benchmarks.longmemeval.run import mrr_at_10

        ranked = ["notes/b.md"]
        assert mrr_at_10(ranked, {"notes/a.md"}) == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# regression_guard.py tests
# ---------------------------------------------------------------------------


class TestRegressionGuard:
    def _write_baseline(self, path: Path, metrics: dict) -> None:
        payload = {"results": metrics}
        path.write_text(json.dumps(payload), encoding="utf-8")

    def test_guard_passes_when_equal(self) -> None:
        from benchmarks.longmemeval.regression_guard import check_regression

        metrics = {
            "recall_at_1": 0.8,
            "recall_at_5": 0.9,
            "recall_at_10": 1.0,
            "mrr_at_10": 0.85,
            "abstention_accuracy": 1.0,
        }
        # result == baseline → no drop → should pass
        passed, report = check_regression(metrics, metrics, tolerance=0.05)
        assert passed, f"Expected pass, got: {report}"

    def test_guard_passes_within_tolerance(self) -> None:
        from benchmarks.longmemeval.regression_guard import check_regression

        baseline = {
            "recall_at_1": 0.8,
            "recall_at_5": 0.9,
            "recall_at_10": 1.0,
            "mrr_at_10": 0.85,
            "abstention_accuracy": 1.0,
        }
        # drop exactly 0.05 → still passes (<=)
        result = {k: v - 0.05 for k, v in baseline.items()}
        passed, report = check_regression(result, baseline, tolerance=0.05)
        assert passed, f"Expected pass at tolerance boundary, got: {report}"

    def test_guard_fails_below_tolerance(self) -> None:
        from benchmarks.longmemeval.regression_guard import check_regression

        baseline = {
            "recall_at_1": 0.8,
            "recall_at_5": 0.9,
            "recall_at_10": 1.0,
            "mrr_at_10": 0.85,
            "abstention_accuracy": 1.0,
        }
        # drop 0.06 on recall_at_1 → should fail
        result = dict(baseline)
        result["recall_at_1"] = 0.74  # drop = 0.06
        passed, report = check_regression(result, baseline, tolerance=0.05)
        assert not passed
        assert "recall_at_1" in report

    def test_guard_main_exit_0_on_pass(self, tmp_path: Path) -> None:
        """CLI main() exits 0 when fresh >= baseline - tolerance."""
        from benchmarks.longmemeval.regression_guard import main

        metrics = {
            "recall_at_1": 0.8,
            "recall_at_5": 0.9,
            "recall_at_10": 1.0,
            "mrr_at_10": 0.85,
            "abstention_accuracy": 1.0,
        }
        baseline_path = tmp_path / "baseline.json"
        result_path = tmp_path / "result.json"
        self._write_baseline(baseline_path, metrics)
        result_path.write_text(json.dumps({"results": metrics}), encoding="utf-8")

        rc = main(["--baseline", str(baseline_path), str(result_path)])
        assert rc == 0

    def test_guard_main_exit_1_on_fail(self, tmp_path: Path) -> None:
        from benchmarks.longmemeval.regression_guard import main

        baseline_metrics = {
            "recall_at_1": 0.8,
            "recall_at_5": 0.9,
            "recall_at_10": 1.0,
            "mrr_at_10": 0.85,
            "abstention_accuracy": 1.0,
        }
        bad_result = dict(baseline_metrics)
        bad_result["recall_at_10"] = 0.0  # severe drop

        baseline_path = tmp_path / "baseline.json"
        result_path = tmp_path / "result.json"
        self._write_baseline(baseline_path, baseline_metrics)
        result_path.write_text(
            json.dumps({"results": bad_result}), encoding="utf-8"
        )

        rc = main(["--baseline", str(baseline_path), str(result_path)])
        assert rc == 1

    def test_baseline_json_has_nonzero_recall(self) -> None:
        """The committed baseline.json must have non-zero recall_at_1."""
        data = _load_baseline()
        assert data["results"]["recall_at_1"] > 0.0, (
            "baseline.json still has placeholder zeros — run run.py --write-baseline"
        )

    def test_baseline_json_notes_documents_dataset_path(self) -> None:
        """baseline.json notes must mention --dataset-path usage."""
        data = _load_baseline()
        assert "--dataset-path" in data["notes"], (
            "baseline.json notes must document the --dataset-path workflow"
        )


# ---------------------------------------------------------------------------
# Official-dataset path tests (run_benchmark_official)
# ---------------------------------------------------------------------------


def _make_official_dataset(tmp_path: Path) -> Path:
    """Write a minimal official-format LongMemEval JSON for testing."""
    cases = [
        {
            "question": "what is FTS5 full text search ranking",
            "answer": "BM25",
            "evidence_files": [
                {
                    "path": "notes/fts5.md",
                    "content": "FTS5 uses BM25 ranking for full text search in SQLite.",
                }
            ],
            "case_type": "single-session-user",
        },
        {
            "question": "reciprocal rank fusion RRF formula",
            "answer": "1/(k + rank)",
            "evidence_files": [
                {
                    "path": "notes/rrf.md",
                    "content": "Reciprocal Rank Fusion score is 1/(k + rank) where k=60.",
                }
            ],
            "case_type": "multi-session-user",
        },
        {
            "question": "luminescent crystallography prism quantum photon",
            "answer": "",
            "evidence_files": [],
            "case_type": "abstention",
        },
    ]
    dataset_path = tmp_path / "longmemeval.json"
    dataset_path.write_text(json.dumps(cases), encoding="utf-8")
    return dataset_path


class TestOfficialDatasetRunner:
    """run_benchmark_official() must load the official format and compute metrics."""

    def test_official_runner_importable(self) -> None:
        from benchmarks.longmemeval.run import run_benchmark_official  # noqa: F401

    def test_official_returns_dataset_source(self, tmp_path: Path) -> None:
        from benchmarks.longmemeval.run import run_benchmark_official

        dataset_path = _make_official_dataset(tmp_path)
        payload = run_benchmark_official(dataset_path=dataset_path)
        assert payload["dataset_source"] == "official-longmemeval"

    def test_official_returns_required_metric_keys(self, tmp_path: Path) -> None:
        from benchmarks.longmemeval.run import run_benchmark_official

        dataset_path = _make_official_dataset(tmp_path)
        payload = run_benchmark_official(dataset_path=dataset_path)
        for key in ("recall_at_1", "recall_at_5", "recall_at_10", "mrr_at_10"):
            assert key in payload["results"], f"missing metric key: {key}"

    def test_official_case_count_matches_non_zero_content(self, tmp_path: Path) -> None:
        from benchmarks.longmemeval.run import run_benchmark_official

        dataset_path = _make_official_dataset(tmp_path)
        payload = run_benchmark_official(dataset_path=dataset_path)
        # 3 cases total: 2 non-abstention + 1 abstention
        assert payload["results"]["case_count"] == 3

    def test_official_abstention_count(self, tmp_path: Path) -> None:
        from benchmarks.longmemeval.run import run_benchmark_official

        dataset_path = _make_official_dataset(tmp_path)
        payload = run_benchmark_official(dataset_path=dataset_path)
        assert payload["results"]["abstention_count"] == 1

    def test_official_recall_is_float(self, tmp_path: Path) -> None:
        from benchmarks.longmemeval.run import run_benchmark_official

        dataset_path = _make_official_dataset(tmp_path)
        payload = run_benchmark_official(dataset_path=dataset_path)
        assert isinstance(payload["results"]["recall_at_10"], float)

    def test_official_output_path_written(self, tmp_path: Path) -> None:
        from benchmarks.longmemeval.run import run_benchmark_official

        dataset_path = _make_official_dataset(tmp_path)
        out_path = tmp_path / "out.json"
        run_benchmark_official(dataset_path=dataset_path, output_path=out_path)
        assert out_path.exists()
        written = json.loads(out_path.read_text(encoding="utf-8"))
        assert written["dataset_source"] == "official-longmemeval"

    def test_official_benchmark_key_present(self, tmp_path: Path) -> None:
        from benchmarks.longmemeval.run import run_benchmark_official

        dataset_path = _make_official_dataset(tmp_path)
        payload = run_benchmark_official(dataset_path=dataset_path)
        assert payload["benchmark"] == "longmemeval"

    def test_cli_dataset_path_flag_accepted(self, tmp_path: Path) -> None:
        """main() must accept --dataset-path without error."""
        import subprocess

        dataset_path = _make_official_dataset(tmp_path)
        out_path = tmp_path / "cli_out.json"
        result = subprocess.run(
            [
                sys.executable,
                str(_BENCH_DIR / "run.py"),
                "--dataset-path",
                str(dataset_path),
                "--output",
                str(out_path),
            ],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, f"stderr: {result.stderr}"
        written = json.loads(out_path.read_text(encoding="utf-8"))
        assert written["dataset_source"] == "official-longmemeval"
