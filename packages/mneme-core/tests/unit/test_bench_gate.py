"""Tests for the seven-surface Mneme 3.6 benchmark gate."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from mneme_core.bench.gate import (
    SURFACES,
    GateConfig,
    _retrieval_metrics_meet_thresholds,
    main,
    run_all,
    run_retrieval_latency,
)


@pytest.fixture
def quick_config() -> GateConfig:
    return GateConfig(
        seed=42,
        docs_per_topic=3,
        queries_per_topic=2,
        cutoff=3,
        latency_samples=5,
    )


def test_all_runs_exactly_seven_synthetic_surfaces(quick_config: GateConfig) -> None:
    payload = run_all(quick_config)
    surfaces = payload["surfaces"]
    assert isinstance(surfaces, dict)
    assert payload["surface_count"] == 7
    configuration = payload["configuration"]
    assert isinstance(configuration, dict)
    assert configuration["docs_per_topic"] == 3
    assert set(surfaces) == set(SURFACES)
    assert payload["synthetic_inputs_only"] is True
    assert payload["passed"] is True
    hardware = payload["hardware"]
    assert isinstance(hardware, dict)
    assert hardware["mneme_bench_seed"] == 42
    for result in surfaces.values():
        assert isinstance(result, dict)
        provenance = result["provenance"]
        assert isinstance(provenance, dict)
        assert provenance["synthetic"] is True
        assert provenance["deterministic_input"] is True


def test_quality_and_ablation_report_required_ir_metrics(
    quick_config: GateConfig,
) -> None:
    payload = run_all(quick_config)
    surfaces = payload["surfaces"]
    assert isinstance(surfaces, dict)
    quality = surfaces["retrieval-quality"]
    assert isinstance(quality, dict)
    assert quality["retrieval_path"] == "python-core-production-fts5"
    assert quality["experimental_backends_included"] is False
    metrics = quality["metrics"]
    assert isinstance(metrics, dict)
    assert {"recall_at_3", "precision_at_3", "mrr", "ndcg_at_3"} <= set(metrics)
    thresholds = quality["thresholds"]
    assert isinstance(thresholds, dict)
    assert thresholds == {
        "recall_at_3": 0.95,
        "precision_at_3": 0.09,
        "mrr": 0.65,
        "ndcg_at_3": 0.70,
    }

    ablation_surface = surfaces["backend-contribution-ablation"]
    assert isinstance(ablation_surface, dict)
    assert "backend_contribution" in ablation_surface
    assert "ablation" in ablation_surface
    contribution = ablation_surface["backend_contribution"]
    assert isinstance(contribution, dict)
    per_backend = contribution["per_backend"]
    assert isinstance(per_backend, dict)
    assert {"fts5", "feature_hash_lexical"} <= set(per_backend)
    assert ablation_surface["feature_hash_classification"] == (
        "lexical-vector-surrogate-not-semantic-model"
    )


def test_index_and_memory_surfaces_report_resource_metrics(
    quick_config: GateConfig,
) -> None:
    payload = run_all(quick_config)
    surfaces = payload["surfaces"]
    assert isinstance(surfaces, dict)
    index_surface = surfaces["index-build"]
    memory_surface = surfaces["memory-footprint"]
    assert isinstance(index_surface, dict)
    assert isinstance(memory_surface, dict)
    index_metrics = index_surface["metrics"]
    memory_metrics = memory_surface["metrics"]
    assert isinstance(index_metrics, dict)
    assert isinstance(memory_metrics, dict)
    assert index_metrics["build_time_ms"] > 0
    assert index_metrics["index_size_bytes"] > 0
    assert memory_metrics["python_peak_delta_bytes"] > 0


def test_schema_surfaces_do_not_claim_official_dataset_scores(
    quick_config: GateConfig,
) -> None:
    payload = run_all(quick_config)
    surfaces = payload["surfaces"]
    assert isinstance(surfaces, dict)
    for name in ("longmemeval-schema", "locomo-schema"):
        result = surfaces[name]
        assert isinstance(result, dict)
        provenance = result["provenance"]
        assert isinstance(provenance, dict)
        assert provenance["official_dataset_downloaded"] is False
        assert provenance["dataset_source"] == "synthetic-official-schema-contract-fixture"


def test_latency_budget_fails_closed(quick_config: GateConfig) -> None:
    strict = GateConfig(
        seed=quick_config.seed,
        docs_per_topic=quick_config.docs_per_topic,
        queries_per_topic=quick_config.queries_per_topic,
        cutoff=quick_config.cutoff,
        latency_samples=1,
        latency_budget_ms=1e-12,
    )
    assert run_retrieval_latency(strict)["passed"] is False


def test_catastrophic_retrieval_metrics_fail_quality_thresholds() -> None:
    metrics: dict[str, object] = {
        "recall_at_10": 0.1,
        "precision_at_10": 0.01,
        "mrr": 0.1,
        "ndcg_at_10": 0.1,
        "queries_evaluated": 50,
    }

    assert _retrieval_metrics_meet_thresholds(metrics, k=10) is False


def test_cli_writes_strict_json_without_nan(tmp_path: Path) -> None:
    output = tmp_path / "gate.json"
    exit_code = main(
        [
            "index-build",
            "--docs-per-topic",
            "2",
            "--queries-per-topic",
            "1",
            "--cutoff",
            "2",
            "--latency-samples",
            "1",
            "--output",
            str(output),
        ]
    )
    assert exit_code == 0
    raw = output.read_text(encoding="utf-8")
    assert "NaN" not in raw
    parsed = json.loads(raw)
    assert parsed["passed"] is True
    assert parsed["configuration"]["docs_per_topic"] == 2
    assert parsed["hardware"]["mneme_bench_seed"] == 42


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"docs_per_topic": 0}, "docs_per_topic"),
        ({"docs_per_topic": 1, "queries_per_topic": 2}, "must not exceed"),
        ({"cutoff": 0}, "cutoff"),
        ({"latency_samples": 0}, "latency_samples"),
    ],
)
def test_invalid_gate_config_rejected(kwargs: dict[str, int], message: str) -> None:
    with pytest.raises(ValueError, match=message):
        GateConfig(**kwargs)
