"""LongMemEval standalone benchmark runner.

Builds an FTS5 index over the synthetic 6-doc fixture in a temp directory,
runs the 8 test queries through fts5_search, computes Recall@1/5/10, MRR@10,
and abstention_accuracy, then writes results to baseline.json.

No imports from mneme-mcp.  No network.  No LLM calls.

Usage (from repo root):

    python benchmarks/longmemeval/run.py
    python benchmarks/longmemeval/run.py --write-baseline
    python benchmarks/longmemeval/run.py --output path/to/out.json
    python benchmarks/longmemeval/run.py --dataset-path path/to/longmemeval.json
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

# Allow running as a script from the repo root without package install.
_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_REPO_ROOT / "packages" / "mneme-core" / "src"))

from mneme_core import __version__ as _MNEME_VERSION  # noqa: E402
from mneme_core.fts5.indexer import (  # noqa: E402
    IndexerConfig,
    connect,
    ensure_schema,
    index_vault,
)
from mneme_core.retrieval.rrf import build_fts5_query, fts5_search  # noqa: E402

try:
    from .loader import build_synthetic_fixture, synthetic_fixture_docs  # noqa: E402
except ImportError:
    # Running as a script (python run.py) rather than as a module (python -m …).
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from loader import build_synthetic_fixture, synthetic_fixture_docs  # type: ignore[no-redef]

_BASELINE_PATH = Path(__file__).parent / "baseline.json"

# ---------------------------------------------------------------------------
# Pure metric helpers (exported so tests can import them directly)
# ---------------------------------------------------------------------------


def recall_at_k(ranked_paths: list[str], gold_paths: set[str], k: int) -> float:
    """Return 1.0 if any gold path's filename appears in ranked_paths[:k]."""
    if not gold_paths:
        return 0.0
    top_stems = {Path(p).name for p in ranked_paths[:k]}
    gold_stems = {Path(p).name for p in gold_paths}
    return 1.0 if top_stems & gold_stems else 0.0


def mrr_at_10(ranked_paths: list[str], gold_paths: set[str]) -> float:
    """Reciprocal rank of the first gold hit in ranked_paths[:10], or 0.0."""
    if not gold_paths:
        return 0.0
    gold_stems = {Path(p).name for p in gold_paths}
    for rank, path in enumerate(ranked_paths[:10], start=1):
        if Path(path).name in gold_stems:
            return 1.0 / rank
    return 0.0


# ---------------------------------------------------------------------------
# Vault materialisation
# ---------------------------------------------------------------------------


def _materialize_docs(docs: list[dict[str, Any]], vault_root: Path) -> None:
    """Write synthetic docs as markdown files under vault_root."""
    vault_root.mkdir(parents=True, exist_ok=True)
    for doc in docs:
        full_path = vault_root / doc["path"]
        full_path.parent.mkdir(parents=True, exist_ok=True)
        body = (
            f"---\ntitle: {doc['title']}\ntype: reference\n---\n\n"
            f"# {doc['title']}\n\n{doc['body']}\n"
        )
        full_path.write_text(body, encoding="utf-8")


# ---------------------------------------------------------------------------
# Core benchmark logic
# ---------------------------------------------------------------------------


def run_benchmark(
    *,
    write_baseline: bool = False,
    output_path: Path | None = None,
) -> dict[str, Any]:
    """Build FTS5 index over the synthetic fixture and evaluate all 8 cases.

    Returns the full result payload dict (same shape as baseline.json).
    When write_baseline is True, writes results to baseline.json (or
    output_path when supplied).
    """
    cases = build_synthetic_fixture()
    docs = synthetic_fixture_docs()

    with tempfile.TemporaryDirectory(prefix="mneme-longmemeval-run-") as tmp:
        tmp_path = Path(tmp)
        vault_root = tmp_path / "vault"
        db_path = tmp_path / ".mneme" / "fts5.sqlite"

        _materialize_docs(docs, vault_root)

        conn = connect(db_path)
        try:
            ensure_schema(conn)
            index_vault(conn, IndexerConfig(vault_root=vault_root, db_path=db_path))
        finally:
            conn.close()

        # Evaluate each case via fts5_search (FTS5 leg only, no RRF).
        r1_scores: list[float] = []
        r5_scores: list[float] = []
        r10_scores: list[float] = []
        rr_scores: list[float] = []
        abstention_correct = 0
        abstention_total = 0
        by_type: dict[str, dict[str, Any]] = {}

        for case in cases:
            hits = fts5_search(case.query, db_path, limit=10)
            ranked = [h.path for h in hits]
            ct = case.case_type

            if case.is_abstention():
                abstention_total += 1
                if len(hits) == 0:
                    abstention_correct += 1
                by_type.setdefault(ct, {"count": 0})
                by_type[ct]["count"] += 1
                continue

            gold = set(case.gold_doc_paths)
            r1 = recall_at_k(ranked, gold, 1)
            r5 = recall_at_k(ranked, gold, 5)
            r10 = recall_at_k(ranked, gold, 10)
            rr = mrr_at_10(ranked, gold)

            r1_scores.append(r1)
            r5_scores.append(r5)
            r10_scores.append(r10)
            rr_scores.append(rr)

            by_type.setdefault(ct, {"recall_at_10": [], "count": 0})
            by_type[ct]["recall_at_10"].append(r10)  # type: ignore[union-attr]
            by_type[ct]["count"] += 1

    def _mean(vals: list[float]) -> float:
        return sum(vals) / len(vals) if vals else 0.0

    by_type_agg: dict[str, Any] = {}
    for ct, data in by_type.items():
        r10_list = data.get("recall_at_10", [])
        # Abstention cases have no gold docs, so recall is undefined (not 0.0).
        # Use None to distinguish "no data" from a real zero-recall failure.
        if isinstance(r10_list, list) and len(r10_list) == 0 and ct == "abstention":
            recall_val: float | None = None
        else:
            recall_val = _mean(r10_list) if isinstance(r10_list, list) else r10_list
        by_type_agg[ct] = {
            "recall_at_10": recall_val,
            "count": data["count"],
        }

    abstention_acc = abstention_correct / abstention_total if abstention_total > 0 else 1.0

    payload: dict[str, Any] = {
        "benchmark": "longmemeval",
        "recorded_at": datetime.now(UTC).strftime("%Y-%m-%d"),
        "dataset_source": "synthetic-fixture",
        "metadata": {
            "vault_doc_count": len(docs),
            "index_locale": "identity",
            "mneme_version": _MNEME_VERSION,
        },
        "results": {
            "recall_at_1": _mean(r1_scores),
            "recall_at_5": _mean(r5_scores),
            "recall_at_10": _mean(r10_scores),
            "mrr_at_10": _mean(rr_scores),
            "abstention_accuracy": abstention_acc,
            "case_count": len(cases),
            "abstention_count": abstention_total,
            "by_case_type": by_type_agg,
        },
        "notes": (
            "Results recorded by benchmarks/longmemeval/run.py. "
            "No superiority claim is made — these numbers reflect FTS5-only retrieval "
            "on the synthetic fixture. Real-dataset numbers require the official "
            "LongMemEval download (see README.md)."
        ),
    }

    dest = output_path if output_path is not None else (_BASELINE_PATH if write_baseline else None)
    if dest is not None:
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    return payload


# ---------------------------------------------------------------------------
# Official LongMemEval dataset runner
# ---------------------------------------------------------------------------

# Official dataset format (public LongMemEval download):
#   [{"question": str, "answer": str,
#     "evidence_files": [{"path": str, "content": str}],
#     "case_type": str}, ...]
#
# For each case: evidence_files are written as .md files in a per-case tmp
# dir, indexed with index_vault, then fts5_search is called with
# build_fts5_query(question). Gold paths = all evidence_files[*].path.
# Abstention cases have evidence_files == [] and answer == "".


def _load_official_dataset(path: Path) -> list[dict[str, Any]]:
    """Load the official LongMemEval JSON. Raises ValueError on bad input."""
    text = path.read_text(encoding="utf-8")
    try:
        raw = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid JSON in dataset file {path}: {exc}") from exc
    if not isinstance(raw, list):
        raise ValueError(f"Dataset must be a JSON list, got {type(raw).__name__}")
    return [item for item in raw if isinstance(item, dict)]


def run_benchmark_official(
    *,
    dataset_path: Path,
    output_path: Path | None = None,
) -> dict[str, Any]:
    """Evaluate FTS5 recall against the official LongMemEval dataset format.

    For each case the evidence_files are materialised as markdown files in a
    per-case temporary vault, indexed, then queried with fts5_search.  Gold
    document paths are taken from evidence_files[*].path.  Abstention cases
    (evidence_files == []) are scored separately.

    Returns a result payload with dataset_source='official-longmemeval'.
    When output_path is supplied the JSON is also written there.
    """
    cases = _load_official_dataset(dataset_path)

    r1_scores: list[float] = []
    r5_scores: list[float] = []
    r10_scores: list[float] = []
    rr_scores: list[float] = []
    abstention_correct = 0
    abstention_total = 0
    by_type: dict[str, dict[str, Any]] = {}
    total_doc_count = 0

    with tempfile.TemporaryDirectory(prefix="mneme-longmemeval-official-") as tmp:
        tmp_root = Path(tmp)

        for case_idx, case in enumerate(cases):
            question: str = case.get("question", "")
            evidence_files: list[dict[str, Any]] = case.get("evidence_files") or []
            case_type: str = case.get("case_type", "single-session-user")
            is_abstention = (case_type == "abstention") or not evidence_files

            # Materialise evidence files in an isolated per-case vault.
            case_vault = tmp_root / f"case-{case_idx}"
            case_vault.mkdir(parents=True, exist_ok=True)
            db_path = case_vault / ".mneme" / "fts5.sqlite"

            gold_paths: list[str] = []
            for ef in evidence_files:
                ef_path: str = ef.get("path", f"doc-{case_idx}.md")
                ef_content: str = ef.get("content", "")
                full = case_vault / ef_path
                full.parent.mkdir(parents=True, exist_ok=True)
                # Wrap plain content in minimal frontmatter so the indexer
                # extracts a title and body without error.
                stem = Path(ef_path).stem
                md_body = (
                    f"---\ntitle: {stem}\ntype: reference\n---\n\n"
                    f"# {stem}\n\n{ef_content}\n"
                )
                full.write_text(md_body, encoding="utf-8")
                gold_paths.append(ef_path)

            total_doc_count += len(evidence_files)

            conn = connect(db_path)
            try:
                ensure_schema(conn)
                index_vault(conn, IndexerConfig(vault_root=case_vault, db_path=db_path))
            finally:
                conn.close()

            # Query: build FTS5 query from the question text.
            fts_query = build_fts5_query(question)
            if fts_query:
                hits = fts5_search(question, db_path, limit=10)
            else:
                hits = []
            ranked = [h.path for h in hits]

            if is_abstention:
                abstention_total += 1
                if len(hits) == 0:
                    abstention_correct += 1
                by_type.setdefault(case_type, {"count": 0})
                by_type[case_type]["count"] += 1
                continue

            gold = set(gold_paths)
            r1 = recall_at_k(ranked, gold, 1)
            r5 = recall_at_k(ranked, gold, 5)
            r10 = recall_at_k(ranked, gold, 10)
            rr = mrr_at_10(ranked, gold)

            r1_scores.append(r1)
            r5_scores.append(r5)
            r10_scores.append(r10)
            rr_scores.append(rr)

            by_type.setdefault(case_type, {"recall_at_10": [], "count": 0})
            by_type[case_type]["recall_at_10"].append(r10)  # type: ignore[union-attr]
            by_type[case_type]["count"] += 1

    def _mean(vals: list[float]) -> float:
        return sum(vals) / len(vals) if vals else 0.0

    by_type_agg: dict[str, Any] = {}
    for ct, data in by_type.items():
        r10_list = data.get("recall_at_10", [])
        # Abstention cases have no gold docs, so recall is undefined (not 0.0).
        # Use None to distinguish "no data" from a real zero-recall failure.
        if isinstance(r10_list, list) and len(r10_list) == 0 and ct == "abstention":
            recall_val: float | None = None
        else:
            recall_val = _mean(r10_list) if isinstance(r10_list, list) else r10_list
        by_type_agg[ct] = {
            "recall_at_10": recall_val,
            "count": data["count"],
        }

    abstention_acc = abstention_correct / abstention_total if abstention_total > 0 else 1.0

    payload: dict[str, Any] = {
        "benchmark": "longmemeval",
        "recorded_at": datetime.now(UTC).strftime("%Y-%m-%d"),
        "dataset_source": "official-longmemeval",
        "metadata": {
            "vault_doc_count": total_doc_count,
            "index_locale": "identity",
            "mneme_version": _MNEME_VERSION,
            "dataset_path": str(dataset_path),
        },
        "results": {
            "recall_at_1": _mean(r1_scores),
            "recall_at_5": _mean(r5_scores),
            "recall_at_10": _mean(r10_scores),
            "mrr_at_10": _mean(rr_scores),
            "abstention_accuracy": abstention_acc,
            "case_count": len(cases),
            "abstention_count": abstention_total,
            "by_case_type": by_type_agg,
        },
        "notes": (
            "Results recorded by benchmarks/longmemeval/run.py against the official "
            "LongMemEval dataset. No superiority claim is made — these numbers reflect "
            "FTS5-only retrieval on per-case evidence vaults."
        ),
    }

    if output_path is not None:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )

    return payload


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser(
        description="LongMemEval standalone benchmark runner (FTS5, synthetic fixture)."
    )
    parser.add_argument(
        "--write-baseline",
        action="store_true",
        default=False,
        help="Write results to benchmarks/longmemeval/baseline.json.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Optional path to write JSON output.",
    )
    parser.add_argument(
        "--dataset-path",
        type=Path,
        default=None,
        metavar="PATH",
        help=(
            "Path to an official LongMemEval JSON file "
            "[{\"question\", \"answer\", \"evidence_files\", \"case_type\"}, ...]. "
            "When provided, runs against the real dataset and sets "
            "dataset_source='official-longmemeval'. "
            "When omitted, the built-in synthetic fixture is used."
        ),
    )
    args = parser.parse_args()

    output_path: Path | None = args.output
    dataset_path: Path | None = args.dataset_path
    write_baseline: bool = args.write_baseline

    if dataset_path is not None:
        payload = run_benchmark_official(
            dataset_path=dataset_path,
            output_path=output_path,
        )
        if output_path is None:
            sys.stdout.write(json.dumps(payload, indent=2, ensure_ascii=False) + "\n")
    else:
        payload = run_benchmark(write_baseline=write_baseline, output_path=output_path)
        if output_path is None and not write_baseline:
            sys.stdout.write(json.dumps(payload, indent=2, ensure_ascii=False) + "\n")

    return 0


if __name__ == "__main__":
    sys.exit(main())
