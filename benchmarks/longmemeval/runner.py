"""LongMemEval evaluation runner.

Measures Recall@1/5/10 and MRR@10 for mneme retrieval against LongMemEval
cases. Works with only the FTS5 leg active (no dense adapter required).

Usage - synthetic fixture (CI-safe, no download):

    python -m benchmarks.longmemeval.runner

Usage - real dataset (requires download, see README.md):

    python -m benchmarks.longmemeval.runner --dataset path/to/longmemeval.json

Write baseline from synthetic fixture:

    python -m benchmarks.longmemeval.runner --write-baseline
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

# Allow running from repo root without installing.
_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_REPO_ROOT / "packages" / "mneme-core" / "src"))

from mneme_core import __version__ as _mneme_version  # noqa: E402
from mneme_core.fts5.indexer import (  # noqa: E402
    IndexerConfig,
    connect,
    ensure_schema,
    index_vault,
)
from mneme_core.retrieval.rrf import Hit, RetrievalConfig, retrieve  # noqa: E402
from mneme_core.vault.config import VaultConfig  # noqa: E402

from .loader import (  # noqa: E402
    LongMemEvalCase,
    build_synthetic_fixture,
    load_dataset,
    synthetic_fixture_docs,
)

_BASELINE_PATH = Path(__file__).parent / "baseline.json"


@dataclass
class EvalResult:
    """Aggregate retrieval quality metrics over an evaluation set.

    Attributes:
        recall_at_1: fraction of cases where a gold doc appears in top-1.
        recall_at_5: fraction of cases where a gold doc appears in top-5.
        recall_at_10: fraction of cases where a gold doc appears in top-10.
        mrr_at_10: mean reciprocal rank at cutoff 10.
        abstention_accuracy: fraction of abstention cases correctly handled
            (retrieve returns empty list).
        by_case_type: per-type breakdown of recall_at_10 and case counts.
        case_count: total evaluation cases.
        abstention_count: number of abstention cases in the set.
    """

    recall_at_1: float
    recall_at_5: float
    recall_at_10: float
    mrr_at_10: float
    abstention_accuracy: float
    by_case_type: dict[str, dict[str, Any]]
    case_count: int
    abstention_count: int


def _hit_paths(hits: list[Hit]) -> list[str]:
    """Extract Path-normalized relative paths from hits."""
    return [h.path for h in hits]


def _recall_at_k(ranked_paths: list[str], gold_paths: set[str], k: int) -> float:
    """1.0 if any gold_path appears in ranked_paths[:k], else 0.0."""
    if not gold_paths:
        return 0.0
    top_k = set(ranked_paths[:k])
    # Normalize: compare by filename stem to tolerate path prefix differences.
    top_k_stems = {Path(p).name for p in top_k}
    gold_stems = {Path(p).name for p in gold_paths}
    return 1.0 if top_k_stems & gold_stems else 0.0


def _reciprocal_rank(ranked_paths: list[str], gold_paths: set[str], cutoff: int) -> float:
    """Reciprocal rank of the first gold hit within cutoff, or 0.0."""
    if not gold_paths:
        return 0.0
    gold_stems = {Path(p).name for p in gold_paths}
    for rank, path in enumerate(ranked_paths[:cutoff], start=1):
        if Path(path).name in gold_stems:
            return 1.0 / rank
    return 0.0


def _materialize_docs(docs: list[dict[str, Any]], vault_root: Path) -> None:
    """Write synthetic docs as markdown files under vault_root."""
    vault_root.mkdir(parents=True, exist_ok=True)
    for doc in docs:
        path_rel = doc["path"]
        full_path = vault_root / path_rel
        full_path.parent.mkdir(parents=True, exist_ok=True)
        body = (
            f"---\ntitle: {doc['title']}\ntype: reference\n---\n\n"
            f"# {doc['title']}\n\n{doc['body']}\n"
        )
        full_path.write_text(body, encoding="utf-8")


def run_evaluation(
    cases: list[LongMemEvalCase],
    vault: VaultConfig,
    config: RetrievalConfig,
) -> EvalResult:
    """Run evaluation over all cases and return aggregated EvalResult.

    For each non-abstention case: call retrieve(case.query, config); check
    if any gold_doc_path appears in the top-1/5/10 returned paths.
    For abstention cases: correct if retrieve returns an empty list.
    """
    r1_scores: list[float] = []
    r5_scores: list[float] = []
    r10_scores: list[float] = []
    rr_scores: list[float] = []
    abstention_correct = 0
    abstention_total = 0

    by_type: dict[str, dict[str, Any]] = {}

    for case in cases:
        hits = retrieve(case.query, config)
        ranked_paths = _hit_paths(hits)

        ct = case.case_type

        if case.is_abstention():
            abstention_total += 1
            correct = len(hits) == 0
            if correct:
                abstention_correct += 1
            by_type.setdefault(ct, {"recall_at_10": [], "count": 0})
            by_type[ct]["count"] += 1
            continue

        gold = set(case.gold_doc_paths)
        r1 = _recall_at_k(ranked_paths, gold, 1)
        r5 = _recall_at_k(ranked_paths, gold, 5)
        r10 = _recall_at_k(ranked_paths, gold, 10)
        rr = _reciprocal_rank(ranked_paths, gold, 10)

        r1_scores.append(r1)
        r5_scores.append(r5)
        r10_scores.append(r10)
        rr_scores.append(rr)

        by_type.setdefault(ct, {"recall_at_10": [], "count": 0})
        by_type[ct]["recall_at_10"].append(r10)
        by_type[ct]["count"] += 1

    def _mean(vals: list[float]) -> float:
        return sum(vals) / len(vals) if vals else 0.0

    # Aggregate by_type: replace list with mean.
    by_type_agg: dict[str, dict[str, Any]] = {}
    for ct, data in by_type.items():
        r10_list = data.get("recall_at_10", [])
        by_type_agg[ct] = {
            "recall_at_10": _mean(r10_list) if isinstance(r10_list, list) else r10_list,
            "count": data["count"],
        }

    abstention_acc = (
        abstention_correct / abstention_total if abstention_total > 0 else 1.0
    )

    return EvalResult(
        recall_at_1=_mean(r1_scores),
        recall_at_5=_mean(r5_scores),
        recall_at_10=_mean(r10_scores),
        mrr_at_10=_mean(rr_scores),
        abstention_accuracy=abstention_acc,
        by_case_type=by_type_agg,
        case_count=len(cases),
        abstention_count=abstention_total,
    )


def _build_baseline_payload(
    result: EvalResult,
    vault_doc_count: int,
    index_locale: str,
    dataset_source: str,
) -> dict[str, Any]:
    return {
        "benchmark": "longmemeval",
        "recorded_at": datetime.now(UTC).strftime("%Y-%m-%d"),
        "dataset_source": dataset_source,
        "metadata": {
            "vault_doc_count": vault_doc_count,
            "index_locale": index_locale,
            "mneme_version": _mneme_version,
        },
        "results": {
            "recall_at_1": result.recall_at_1,
            "recall_at_5": result.recall_at_5,
            "recall_at_10": result.recall_at_10,
            "mrr_at_10": result.mrr_at_10,
            "abstention_accuracy": result.abstention_accuracy,
            "case_count": result.case_count,
            "abstention_count": result.abstention_count,
            "by_case_type": result.by_case_type,
        },
        "notes": (
            "Results recorded by benchmarks/longmemeval/runner.py. "
            "No superiority claim is made — these numbers reflect FTS5-only retrieval "
            "on the synthetic fixture. Real-dataset numbers require the official "
            "LongMemEval download (see README.md)."
        ),
    }


def _run_with_synthetic_fixture(write_baseline: bool) -> dict[str, Any]:
    """Run evaluation using the built-in synthetic fixture."""
    cases = build_synthetic_fixture()
    docs = synthetic_fixture_docs()

    with tempfile.TemporaryDirectory(prefix="mneme-longmemeval-") as tmp:
        tmp_path = Path(tmp)
        vault_root = tmp_path / "vault"
        db_path = tmp_path / ".mneme" / "fts5.sqlite"

        _materialize_docs(docs, vault_root)

        conn = connect(db_path)
        try:
            ensure_schema(conn)
            index_vault(
                conn,
                IndexerConfig(vault_root=vault_root, db_path=db_path),
            )
        finally:
            conn.close()

        vault = VaultConfig.from_path(vault_root)
        config = RetrievalConfig(
            fts5_db=db_path,
            top_n_final=10,
            min_query_length=3,
        )

        result = run_evaluation(cases, vault, config)

    payload = _build_baseline_payload(
        result,
        vault_doc_count=len(docs),
        index_locale="identity",
        dataset_source="synthetic-fixture",
    )

    if write_baseline:
        _BASELINE_PATH.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )

    return payload


def _run_with_real_dataset(dataset_path: Path) -> dict[str, Any]:
    """Run evaluation against the real LongMemEval dataset.

    The vault is built by writing each case's gold_doc_paths as stub markdown
    documents under a temp vault. This allows the FTS5 index to be built and
    retrieve() to be exercised without requiring a pre-built production vault.
    """
    cases = load_dataset(dataset_path)

    # Collect all unique gold_doc_paths to build the vault stub.
    all_paths: set[str] = set()
    for case in cases:
        all_paths.update(case.gold_doc_paths)

    with tempfile.TemporaryDirectory(prefix="mneme-longmemeval-real-") as tmp:
        tmp_path = Path(tmp)
        vault_root = tmp_path / "vault"
        db_path = tmp_path / ".mneme" / "fts5.sqlite"

        vault_root.mkdir(parents=True)
        # Write stub docs for each gold path so FTS5 can index them.
        for rel_path in all_paths:
            full = vault_root / rel_path
            full.parent.mkdir(parents=True, exist_ok=True)
            stem = Path(rel_path).stem
            body = f"---\ntitle: {stem}\ntype: reference\n---\n\n# {stem}\n\nContent for {stem}.\n"
            full.write_text(body, encoding="utf-8")

        conn = connect(db_path)
        try:
            ensure_schema(conn)
            index_vault(
                conn,
                IndexerConfig(vault_root=vault_root, db_path=db_path),
            )
        finally:
            conn.close()

        vault = VaultConfig.from_path(vault_root)
        config = RetrievalConfig(
            fts5_db=db_path,
            top_n_final=10,
            min_query_length=3,
        )

        result = run_evaluation(cases, vault, config)

    return _build_baseline_payload(
        result,
        vault_doc_count=len(all_paths),
        index_locale="identity",
        dataset_source=str(dataset_path),
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="LongMemEval evaluation runner for mneme retrieval."
    )
    parser.add_argument(
        "--dataset",
        type=Path,
        default=None,
        help=(
            "Path to a LongMemEval JSON file. "
            "When omitted, the built-in synthetic fixture is used."
        ),
    )
    parser.add_argument(
        "--write-baseline",
        action="store_true",
        default=False,
        help="Write results to benchmarks/longmemeval/baseline.json (synthetic fixture only).",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Optional path to write JSON output as UTF-8.",
    )
    args = parser.parse_args()

    if args.dataset is not None:
        payload = _run_with_real_dataset(args.dataset)
    else:
        payload = _run_with_synthetic_fixture(write_baseline=args.write_baseline)

    out = json.dumps(payload, indent=2, ensure_ascii=False) + "\n"

    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(out, encoding="utf-8")
    else:
        sys.stdout.write(out)

    return 0


if __name__ == "__main__":
    sys.exit(main())
