"""Runnable Mneme 3.6 benchmark gate with seven bounded surfaces.

Every built-in dataset is deterministic and synthetic. Timing and memory
values are measured at run time and therefore vary by machine. The official
LongMemEval and LoCoMo probes validate synthetic records shaped to the public
schemas. They do not download, embed, or score either copyrighted dataset.
"""

from __future__ import annotations

import argparse
import json
import math
import tempfile
import time
import tracemalloc
from collections import defaultdict
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Literal, cast

from mneme_core.bench.hardware import capture_hardware
from mneme_core.bench.harness import (
    DatasetSchemaError,
    EvalCase,
    EvalReport,
    load_locomo_official,
    load_longmemeval_official,
    run_eval,
)
from mneme_core.bench.metrics import (
    mean_reciprocal_rank,
    ndcg_at_k,
    percentiles,
    precision_at_k,
    recall_at_k,
)
from mneme_core.bench.synth import SyntheticCorpus, build_synthetic_corpus
from mneme_core.fts5.indexer import IndexerConfig, IndexStats, connect, ensure_schema, index_vault
from mneme_core.retrieval.dense import DenseBackend, build_dense_index
from mneme_core.retrieval.rrf import Hit, RetrievalConfig, fts5_search, retrieve

SurfaceName = Literal[
    "retrieval-quality",
    "retrieval-latency",
    "index-build",
    "memory-footprint",
    "backend-contribution-ablation",
    "longmemeval-schema",
    "locomo-schema",
]

SURFACES: tuple[SurfaceName, ...] = (
    "retrieval-quality",
    "retrieval-latency",
    "index-build",
    "memory-footprint",
    "backend-contribution-ablation",
    "longmemeval-schema",
    "locomo-schema",
)

_LONGMEMEVAL_SCHEMA_URL = (
    "https://github.com/xiaowu0162/LongMemEval/tree/"
    "9e0b455f4ef0e2ab8f2e582289761153549043fc#dataset-format"
)
_LOCOMO_SCHEMA_URL = (
    "https://github.com/snap-research/locomo/tree/"
    "3eb6f2c585f5e1699204e3c3bdf7adc5c28cb376#data"
)

_MIN_RECALL_AT_K = 0.95
_MIN_PRECISION_AT_K = 0.09
_MIN_MRR = 0.65
_MIN_NDCG_AT_K = 0.70


@dataclass(frozen=True)
class GateConfig:
    """Parameters shared by all seven benchmark surfaces."""

    seed: int = 42
    docs_per_topic: int = 50
    queries_per_topic: int = 5
    cutoff: int = 10
    latency_samples: int = 100
    latency_budget_ms: float = 1000.0

    def __post_init__(self) -> None:
        if self.docs_per_topic < 1:
            raise ValueError("docs_per_topic must be >= 1")
        if self.queries_per_topic < 1:
            raise ValueError("queries_per_topic must be >= 1")
        if self.queries_per_topic > self.docs_per_topic:
            raise ValueError("queries_per_topic must not exceed docs_per_topic")
        if self.cutoff < 1:
            raise ValueError("cutoff must be >= 1")
        if self.latency_samples < 1:
            raise ValueError("latency_samples must be >= 1")
        if self.latency_budget_ms <= 0:
            raise ValueError("latency_budget_ms must be > 0")


@dataclass(frozen=True)
class _PreparedCorpus:
    corpus: SyntheticCorpus
    db_path: Path
    stats: IndexStats
    build_time_ms: float
    index_size_bytes: int


def _provenance(
    surface: SurfaceName,
    config: GateConfig,
    *,
    source: str = "mneme-seeded-synthetic-corpus",
    schema_reference: str | None = None,
) -> dict[str, object]:
    payload: dict[str, object] = {
        "surface": surface,
        "dataset_source": source,
        "dataset_kind": "synthetic",
        "synthetic": True,
        "deterministic_input": True,
        "seed": config.seed,
        "result_kind": "local-measurement",
    }
    if schema_reference is not None:
        payload["official_schema_reference"] = schema_reference
        payload["official_dataset_downloaded"] = False
    return payload


def _materialize_corpus(corpus: SyntheticCorpus, vault_root: Path) -> None:
    vault_root.mkdir(parents=True, exist_ok=True)
    for document in corpus.docs:
        content = (
            f"---\nid: {document.id}\ntype: reference\nscope: default\n"
            f"topic: {document.topic}\n---\n\n# {document.title}\n\n{document.body}\n"
        )
        (vault_root / f"{document.id}.md").write_text(content, encoding="utf-8")


def _index_size_bytes(db_path: Path) -> int:
    return sum(
        path.stat().st_size for path in db_path.parent.glob(f"{db_path.name}*") if path.is_file()
    )


def _prepare_corpus(root: Path, config: GateConfig) -> _PreparedCorpus:
    corpus = build_synthetic_corpus(
        seed=config.seed,
        docs_per_topic=config.docs_per_topic,
        queries_per_topic=config.queries_per_topic,
    )
    vault_root = root / "vault"
    db_path = root / "state" / "fts5.sqlite"
    _materialize_corpus(corpus, vault_root)
    started = time.perf_counter_ns()
    connection = connect(db_path)
    try:
        ensure_schema(connection)
        stats = index_vault(
            connection,
            IndexerConfig(vault_root=vault_root, db_path=db_path),
        )
    finally:
        connection.close()
    build_time_ms = (time.perf_counter_ns() - started) / 1_000_000.0
    return _PreparedCorpus(
        corpus=corpus,
        db_path=db_path,
        stats=stats,
        build_time_ms=build_time_ms,
        index_size_bytes=_index_size_bytes(db_path),
    )


def _hit_id(hit: Hit) -> str:
    return Path(hit.path).stem if hit.path.endswith(".md") else str(hit.id)


def _retrieval_config(prepared: _PreparedCorpus, config: GateConfig) -> RetrievalConfig:
    return RetrievalConfig(
        fts5_db=prepared.db_path,
        top_k_per_backend=max(config.cutoff, 50),
        top_n_final=config.cutoff,
    )


def _rankings(
    prepared: _PreparedCorpus,
    config: GateConfig,
    condition: Literal["fts5", "feature-hash", "fused"],
) -> list[list[Hit]]:
    retrieval_config = _retrieval_config(prepared, config)
    dense_backend = (
        None if condition == "fts5" else DenseBackend(build_dense_index(prepared.db_path))
    )
    rankings: list[list[Hit]] = []
    for query in prepared.corpus.queries:
        if condition == "fts5":
            hits = fts5_search(query.text, prepared.db_path, limit=config.cutoff)
        elif condition == "feature-hash":
            assert dense_backend is not None
            hits = dense_backend(query.text, config.cutoff)
        else:
            assert dense_backend is not None
            hits = retrieve(
                query.text,
                retrieval_config,
                dense_backend=dense_backend,
            )
        rankings.append(hits)
    return rankings


def _metric_summary(
    corpus: SyntheticCorpus,
    rankings: Sequence[Sequence[Hit]],
    *,
    k: int,
) -> dict[str, object]:
    recalls: list[float] = []
    precisions: list[float] = []
    ndcgs: list[float] = []
    reciprocal_inputs: list[tuple[list[str], set[str]]] = []
    for query, hits in zip(corpus.queries, rankings, strict=True):
        ranked_ids = [_hit_id(hit) for hit in hits]
        relevant = set(query.relevant_doc_ids)
        recalls.append(recall_at_k(ranked_ids, relevant, k))
        precisions.append(precision_at_k(ranked_ids, relevant, k))
        ndcgs.append(ndcg_at_k(ranked_ids, relevant, k))
        reciprocal_inputs.append((ranked_ids, relevant))
    count = len(corpus.queries)
    return {
        f"recall_at_{k}": sum(recalls) / count if count else 0.0,
        f"precision_at_{k}": sum(precisions) / count if count else 0.0,
        "mrr": mean_reciprocal_rank(reciprocal_inputs, cutoff=k),
        f"ndcg_at_{k}": sum(ndcgs) / count if count else 0.0,
        "queries_evaluated": count,
    }


def _metrics_are_finite(metrics: dict[str, object]) -> bool:
    numeric = [
        value
        for key, value in metrics.items()
        if key != "queries_evaluated" and isinstance(value, (int, float))
    ]
    return bool(numeric) and all(math.isfinite(float(value)) for value in numeric)


def _retrieval_metrics_are_valid(metrics: dict[str, object]) -> bool:
    values = [
        float(value)
        for key, value in metrics.items()
        if (
            key == "mrr"
            or key.startswith("recall_at_")
            or key.startswith("precision_at_")
            or key.startswith("ndcg_at_")
        )
        and isinstance(value, (int, float))
    ]
    return (
        _metrics_are_finite(metrics)
        and bool(values)
        and all(0.0 <= value <= 1.0 for value in values)
        and any(value > 0.0 for value in values)
    )


def _retrieval_quality_thresholds(k: int) -> dict[str, float]:
    return {
        f"recall_at_{k}": _MIN_RECALL_AT_K,
        f"precision_at_{k}": _MIN_PRECISION_AT_K,
        "mrr": _MIN_MRR,
        f"ndcg_at_{k}": _MIN_NDCG_AT_K,
    }


def _retrieval_metrics_meet_thresholds(
    metrics: dict[str, object],
    *,
    k: int,
) -> bool:
    if not _retrieval_metrics_are_valid(metrics):
        return False
    thresholds = _retrieval_quality_thresholds(k)
    for metric, minimum in thresholds.items():
        value = metrics.get(metric)
        if not isinstance(value, (int, float)) or float(value) < minimum:
            return False
    queries_evaluated = metrics.get("queries_evaluated")
    return isinstance(queries_evaluated, int) and queries_evaluated > 0


def run_retrieval_quality(config: GateConfig) -> dict[str, object]:
    """Measure retrieval quality on the shipped production FTS5 path."""
    with tempfile.TemporaryDirectory(prefix="mneme-gate-quality-") as temporary:
        prepared = _prepare_corpus(Path(temporary), config)
        metrics = _metric_summary(
            prepared.corpus,
            _rankings(prepared, config, "fts5"),
            k=config.cutoff,
        )
    return {
        "provenance": _provenance("retrieval-quality", config),
        "retrieval_path": "python-core-production-fts5",
        "experimental_backends_included": False,
        "metrics": metrics,
        "thresholds": _retrieval_quality_thresholds(config.cutoff),
        "passed": _retrieval_metrics_meet_thresholds(metrics, k=config.cutoff)
        and metrics["queries_evaluated"] == len(prepared.corpus.queries),
        "limitations": [
            "Synthetic title-anchored corpus only.",
            "Experimental feature hashing is reported only in the separate ablation surface.",
        ],
    }


def run_retrieval_latency(config: GateConfig) -> dict[str, object]:
    """Measure p50, p95, and p99 of the shipped FTS5 retrieval path."""
    with tempfile.TemporaryDirectory(prefix="mneme-gate-latency-") as temporary:
        prepared = _prepare_corpus(Path(temporary), config)
        retrieval_config = _retrieval_config(prepared, config)
        queries = prepared.corpus.queries
        for query in queries:
            retrieve(query.text, retrieval_config)
        samples_ms: list[float] = []
        result_counts: list[int] = []
        for index in range(config.latency_samples):
            query = queries[index % len(queries)]
            started = time.perf_counter_ns()
            hits = retrieve(query.text, retrieval_config)
            samples_ms.append((time.perf_counter_ns() - started) / 1_000_000.0)
            result_counts.append(len(hits))
    distribution = percentiles(samples_ms, (0.5, 0.95, 0.99))
    p95 = distribution["p95"]
    metrics: dict[str, object] = {
        "samples": len(samples_ms),
        "p50_ms": distribution["p50"],
        "p95_ms": p95,
        "p99_ms": distribution["p99"],
        "mean_result_count": sum(result_counts) / len(result_counts),
        "budget_ms": config.latency_budget_ms,
    }
    return {
        "provenance": _provenance("retrieval-latency", config),
        "retrieval_path": "python-core-production-fts5",
        "metrics": metrics,
        "passed": math.isfinite(p95) and p95 < config.latency_budget_ms,
        "limitations": [
            "Wall-clock latency is hardware and load dependent.",
            "This core surface measures retrieval, not the Claude Stop hook.",
        ],
    }


def run_index_build(config: GateConfig) -> dict[str, object]:
    """Measure FTS5 build time, throughput, and on-disk index size."""
    with tempfile.TemporaryDirectory(prefix="mneme-gate-index-") as temporary:
        prepared = _prepare_corpus(Path(temporary), config)
    indexed = prepared.stats.indexed
    elapsed_s = prepared.build_time_ms / 1000.0
    metrics: dict[str, object] = {
        "documents_expected": len(prepared.corpus.docs),
        "documents_indexed": indexed,
        "documents_skipped_error": prepared.stats.skipped_error,
        "build_time_ms": prepared.build_time_ms,
        "documents_per_second": indexed / elapsed_s if elapsed_s > 0 else 0.0,
        "index_size_bytes": prepared.index_size_bytes,
    }
    return {
        "provenance": _provenance("index-build", config),
        "metrics": metrics,
        "passed": indexed == len(prepared.corpus.docs)
        and prepared.stats.skipped_error == 0
        and prepared.build_time_ms > 0
        and prepared.index_size_bytes > 0,
        "limitations": [
            "Index size includes SQLite sidecar files present after connection close.",
            "Build time is a local wall-clock measurement and is not a committed baseline.",
        ],
    }


def run_memory_footprint(config: GateConfig) -> dict[str, object]:
    """Measure Python allocations for index build, feature hash, and retrieval."""
    tracing_before = tracemalloc.is_tracing()
    if not tracing_before:
        tracemalloc.start()
    tracemalloc.reset_peak()
    baseline_current, _ = tracemalloc.get_traced_memory()
    dense_docs = 0
    with tempfile.TemporaryDirectory(prefix="mneme-gate-memory-") as temporary:
        prepared = _prepare_corpus(Path(temporary), config)
        dense_index = build_dense_index(prepared.db_path)
        dense_docs = len(dense_index.docs)
        retrieval_config = _retrieval_config(prepared, config)
        dense_backend = DenseBackend(dense_index)
        for query in prepared.corpus.queries:
            retrieve(query.text, retrieval_config, dense_backend=dense_backend)
        current_bytes, peak_bytes = tracemalloc.get_traced_memory()
    if not tracing_before:
        tracemalloc.stop()
    peak_delta = max(0, peak_bytes - baseline_current)
    metrics: dict[str, object] = {
        "python_current_bytes": current_bytes,
        "python_peak_bytes": peak_bytes,
        "python_peak_delta_bytes": peak_delta,
        "dense_documents_materialized": dense_docs,
    }
    return {
        "provenance": _provenance("memory-footprint", config),
        "metrics": metrics,
        "passed": peak_delta > 0 and dense_docs == len(prepared.corpus.docs),
        "limitations": [
            "tracemalloc covers Python allocations, not SQLite native pages or total RSS.",
            "The feature-hash index is experimental and disconnected from MCP search.",
        ],
    }


def _backend_contribution(
    corpus: SyntheticCorpus,
    rankings: Sequence[Sequence[Hit]],
    *,
    k: int,
) -> dict[str, object]:
    appearances: defaultdict[str, int] = defaultdict(int)
    unique_appearances: defaultdict[str, int] = defaultdict(int)
    relevant_appearances: defaultdict[str, int] = defaultdict(int)
    contributing_queries: defaultdict[str, set[str]] = defaultdict(set)
    returned_slots = 0
    for query, hits in zip(corpus.queries, rankings, strict=True):
        relevant = set(query.relevant_doc_ids)
        for hit in hits[:k]:
            returned_slots += 1
            raw_sources = hit.sources or [hit.source]
            sources = {
                "feature_hash_lexical" if source == "dense" else source for source in raw_sources
            }
            for source in sources:
                appearances[source] += 1
                contributing_queries[source].add(query.qid)
                if _hit_id(hit) in relevant:
                    relevant_appearances[source] += 1
            if len(sources) == 1:
                unique_appearances[next(iter(sources))] += 1
    backend_names = sorted(appearances)
    return {
        "returned_slots": returned_slots,
        "per_backend": {
            name: {
                "output_appearances": appearances[name],
                "unique_output_appearances": unique_appearances[name],
                "relevant_output_appearances": relevant_appearances[name],
                "contributing_queries": len(contributing_queries[name]),
            }
            for name in backend_names
        },
    }


def _metric_deltas(left: dict[str, object], right: dict[str, object]) -> dict[str, float]:
    deltas: dict[str, float] = {}
    for key, left_value in left.items():
        right_value = right.get(key)
        if (
            key != "queries_evaluated"
            and isinstance(left_value, (int, float))
            and isinstance(right_value, (int, float))
        ):
            deltas[key] = float(left_value) - float(right_value)
    return deltas


def run_backend_contribution_ablation(config: GateConfig) -> dict[str, object]:
    """Report per-backend contribution and FTS5/feature-hash/RRF ablations."""
    with tempfile.TemporaryDirectory(prefix="mneme-gate-ablation-") as temporary:
        prepared = _prepare_corpus(Path(temporary), config)
        fts5_rankings = _rankings(prepared, config, "fts5")
        feature_rankings = _rankings(prepared, config, "feature-hash")
        fused_rankings = _rankings(prepared, config, "fused")
        conditions = {
            "fts5_only": _metric_summary(prepared.corpus, fts5_rankings, k=config.cutoff),
            "feature_hash_lexical_only": _metric_summary(
                prepared.corpus, feature_rankings, k=config.cutoff
            ),
            "rrf_fts5_plus_feature_hash_lexical": _metric_summary(
                prepared.corpus, fused_rankings, k=config.cutoff
            ),
        }
        contribution = _backend_contribution(prepared.corpus, fused_rankings, k=config.cutoff)
    fused = conditions["rrf_fts5_plus_feature_hash_lexical"]
    returned_slots = contribution.get("returned_slots")
    per_backend = contribution.get("per_backend")
    contribution_valid = (
        isinstance(returned_slots, int)
        and returned_slots > 0
        and isinstance(per_backend, dict)
        and {"fts5", "feature_hash_lexical"} <= set(per_backend)
    )
    ablation = {
        "fused_minus_fts5_only": _metric_deltas(fused, conditions["fts5_only"]),
        "fused_minus_feature_hash_only": _metric_deltas(
            fused, conditions["feature_hash_lexical_only"]
        ),
    }
    return {
        "provenance": _provenance("backend-contribution-ablation", config),
        "feature_hash_classification": "lexical-vector-surrogate-not-semantic-model",
        "conditions": conditions,
        "backend_contribution": contribution,
        "ablation": ablation,
        "passed": contribution_valid
        and all(_retrieval_metrics_are_valid(metrics) for metrics in conditions.values()),
        "limitations": [
            "Contribution counts describe result provenance, not causal attribution.",
            "Ablation deltas are valid only for this deterministic synthetic fixture.",
        ],
    }


def _longmemeval_fixture() -> tuple[list[dict[str, object]], dict[str, str]]:
    sessions = {
        "lme-session-1": "The synthetic baseline discussed neutral routing.",
        "lme-session-2": "The amber protocol was reviewed in laboratory delta.",
        "lme-session-3": "Cobalt snapshots use an append only storage format.",
    }
    records: list[dict[str, object]] = []
    specs = (
        (
            "single-session-user_synthetic-001",
            "Where was the amber protocol reviewed?",
            "laboratory delta",
            "lme-session-2",
        ),
        (
            "knowledge-update_synthetic-002",
            "Which storage format protects cobalt snapshots?",
            "append only storage",
            "lme-session-3",
        ),
    )
    for case_id, question, answer, answer_session_id in specs:
        haystack_sessions: list[list[dict[str, object]]] = []
        for session_id, text in sessions.items():
            turn: dict[str, object] = {"role": "user", "content": text}
            if session_id == answer_session_id:
                turn["has_answer"] = True
            haystack_sessions.append([turn])
        records.append(
            {
                "question_id": case_id,
                "question_type": case_id.split("_", 1)[0],
                "question": question,
                "answer": answer,
                "question_date": "2026/01/04",
                "haystack_session_ids": list(sessions),
                "haystack_dates": ["2026/01/01", "2026/01/02", "2026/01/03"],
                "haystack_sessions": haystack_sessions,
                "answer_session_ids": [answer_session_id],
            }
        )
    return records, sessions


def _locomo_fixture() -> tuple[list[dict[str, object]], dict[str, str]]:
    documents = {
        "D1:1": "The cedar key is stored in synthetic vault seven.",
        "D1:2": "The team discussed a neutral lunch schedule.",
        "D2:1": "The quartz report is reviewed every synthetic Tuesday.",
    }
    records: list[dict[str, object]] = [
        {
            "sample_id": "synthetic-conversation-001",
            "conversation": {
                "speaker_a": "Avery",
                "speaker_b": "Blake",
                "session_1_date_time": "2026/01/01",
                "session_1": [
                    {"speaker": "Avery", "dia_id": "D1:1", "text": documents["D1:1"]},
                    {"speaker": "Blake", "dia_id": "D1:2", "text": documents["D1:2"]},
                ],
                "session_2_date_time": "2026/01/02",
                "session_2": [{"speaker": "Blake", "dia_id": "D2:1", "text": documents["D2:1"]}],
            },
            "qa": [
                {
                    "question": "Where is the cedar key stored?",
                    "answer": "synthetic vault seven",
                    "category": 1,
                    "evidence": ["D1:1"],
                },
                {
                    "question": "When is the quartz report reviewed?",
                    "answer": "every synthetic Tuesday",
                    "category": 2,
                    "evidence": ["D2:1"],
                },
            ],
        }
    ]
    return records, documents


def _eval_fixture_documents(
    cases: Sequence[EvalCase],
    documents: dict[str, str],
    *,
    k: int,
) -> EvalReport:
    with tempfile.TemporaryDirectory(prefix="mneme-gate-schema-") as temporary:
        root = Path(temporary)
        vault_root = root / "vault"
        db_path = root / "state" / "fts5.sqlite"
        vault_root.mkdir(parents=True)
        path_to_id: dict[str, str] = {}
        for index, (document_id, content) in enumerate(sorted(documents.items())):
            relative_path = f"fixture-{index:03d}.md"
            path_to_id[relative_path] = document_id
            (vault_root / relative_path).write_text(
                f"---\nscope: default\n---\n\n# Synthetic fixture\n\n{content}\n",
                encoding="utf-8",
            )
        connection = connect(db_path)
        try:
            ensure_schema(connection)
            index_vault(
                connection,
                IndexerConfig(vault_root=vault_root, db_path=db_path),
            )
        finally:
            connection.close()

        def retrieve_ids(query: str) -> list[str | int]:
            return [
                path_to_id[hit.path]
                for hit in fts5_search(query, db_path, limit=k)
                if hit.path in path_to_id
            ]

        return run_eval(cases, retrieve_ids, system_name="mneme-fts5", k=k)


def _report_metrics(report: EvalReport) -> dict[str, object]:
    return {
        f"recall_at_{report.k}": report.mean_recall_at_k,
        f"precision_at_{report.k}": report.mean_precision_at_k,
        "mrr": report.mean_mrr,
        f"ndcg_at_{report.k}": report.mean_ndcg_at_k,
        "cases_evaluated": report.n_cases,
    }


def _negative_schema_probe(
    loader: Callable[[Sequence[object]], list[EvalCase]],
    malformed: Sequence[object],
) -> bool:
    try:
        loader(malformed)
    except DatasetSchemaError:
        return True
    return False


def run_longmemeval_schema(config: GateConfig) -> dict[str, object]:
    """Validate an official-shaped synthetic LongMemEval contract fixture."""
    records, documents = _longmemeval_fixture()
    cases = load_longmemeval_official(records)
    report = _eval_fixture_documents(cases, documents, k=config.cutoff)
    metrics = _report_metrics(report)
    malformed = [dict(records[0])]
    malformed[0].pop("answer_session_ids")
    rejected = _negative_schema_probe(load_longmemeval_official, malformed)
    return {
        "provenance": _provenance(
            "longmemeval-schema",
            config,
            source="synthetic-official-schema-contract-fixture",
            schema_reference=_LONGMEMEVAL_SCHEMA_URL,
        ),
        "schema_validation": {
            "records_validated": len(records),
            "malformed_record_rejected": rejected,
        },
        "metrics": metrics,
        "passed": rejected
        and report.n_cases == len(cases)
        and _retrieval_metrics_are_valid(metrics),
        "limitations": [
            "This is not a score on the LongMemEval dataset.",
            "The fixture validates session-level retrieval schema and plumbing only.",
        ],
    }


def run_locomo_schema(config: GateConfig) -> dict[str, object]:
    """Validate an official-shaped synthetic LoCoMo contract fixture."""
    records, documents = _locomo_fixture()
    cases = load_locomo_official(records)
    report = _eval_fixture_documents(cases, documents, k=config.cutoff)
    metrics = _report_metrics(report)
    malformed = [dict(records[0])]
    malformed[0].pop("qa")
    rejected = _negative_schema_probe(load_locomo_official, malformed)
    return {
        "provenance": _provenance(
            "locomo-schema",
            config,
            source="synthetic-official-schema-contract-fixture",
            schema_reference=_LOCOMO_SCHEMA_URL,
        ),
        "schema_validation": {
            "samples_validated": len(records),
            "qa_cases_emitted": len(cases),
            "malformed_record_rejected": rejected,
        },
        "metrics": metrics,
        "passed": rejected
        and report.n_cases == len(cases)
        and _retrieval_metrics_are_valid(metrics),
        "limitations": [
            "This is not a score on the LoCoMo dataset.",
            "The fixture validates nested conversation and QA retrieval plumbing only.",
        ],
    }


_RUNNERS: dict[SurfaceName, Callable[[GateConfig], dict[str, object]]] = {
    "retrieval-quality": run_retrieval_quality,
    "retrieval-latency": run_retrieval_latency,
    "index-build": run_index_build,
    "memory-footprint": run_memory_footprint,
    "backend-contribution-ablation": run_backend_contribution_ablation,
    "longmemeval-schema": run_longmemeval_schema,
    "locomo-schema": run_locomo_schema,
}


def run_surface(surface: SurfaceName, config: GateConfig) -> dict[str, object]:
    """Run one named benchmark surface."""
    return _RUNNERS[surface](config)


def run_all(config: GateConfig) -> dict[str, object]:
    """Run all seven surfaces and return one aggregate gate payload."""
    results = {surface: run_surface(surface, config) for surface in SURFACES}
    return {
        "schema_version": "mneme-benchmark-gate/1",
        "synthetic_inputs_only": True,
        "surface_count": len(results),
        "configuration": asdict(config),
        "hardware": asdict(capture_hardware(seed=config.seed)),
        "surfaces": results,
        "passed": all(result.get("passed") is True for result in results.values()),
    }


def _write_json(payload: dict[str, object], output: Path | None) -> None:
    serialized = json.dumps(payload, indent=2, sort_keys=True, allow_nan=False) + "\n"
    if output is None:
        print(serialized, end="")
        return
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(serialized, encoding="utf-8")


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entry point for ``python -m mneme_core.bench.gate``."""
    parser = argparse.ArgumentParser(description="Mneme 3.6 seven-surface benchmark gate")
    parser.add_argument("surface", choices=("all", *SURFACES))
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--docs-per-topic", type=int, default=50)
    parser.add_argument("--queries-per-topic", type=int, default=5)
    parser.add_argument("--cutoff", type=int, default=10)
    parser.add_argument("--latency-samples", type=int, default=100)
    parser.add_argument("--latency-budget-ms", type=float, default=1000.0)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    config = GateConfig(
        seed=args.seed,
        docs_per_topic=args.docs_per_topic,
        queries_per_topic=args.queries_per_topic,
        cutoff=args.cutoff,
        latency_samples=args.latency_samples,
        latency_budget_ms=args.latency_budget_ms,
    )
    payload = (
        run_all(config)
        if args.surface == "all"
        else run_surface(cast(SurfaceName, args.surface), config)
    )
    if args.surface != "all":
        payload["configuration"] = asdict(config)
        payload["hardware"] = asdict(capture_hardware(seed=config.seed))
    _write_json(payload, args.output)
    return 0 if payload.get("passed") is True else 1


if __name__ == "__main__":
    raise SystemExit(main())
