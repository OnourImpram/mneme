"""Benchmark A - Retrieval Quality.

Builds the 500-doc synthetic corpus, indexes it with the production
FTS5 indexer, then runs every synthetic query through the production
``retrieve`` pipeline. Reports nDCG@5, Recall@10, and MRR aggregated
across the query set.

A second condition exercises RRF fusion over two backends:

* FTS5 BM25 (always)
* a pure-Python BoW cosine surrogate that stands in for the dense
  embedding backend the standard profile will ship post-v1.0

The BoW backend is intentionally synthetic. The point is to exercise
the production RRF fusion code path under known-relevant data so the
metric for the fused condition is comparable to the FTS5-only baseline
and the regression guard can catch fusion regressions.

Usage:

    python benchmarks/retrieval/run.py --output-format=json --output result.json
    python benchmarks/retrieval/run.py --output-format=table
"""

from __future__ import annotations

import argparse
import json
import math
import sys
import tempfile
from collections import Counter
from pathlib import Path

# Allow running from the repo root without installing the package.
_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_REPO_ROOT / "packages" / "mneme-core" / "src"))

from mneme_core.bench import (  # noqa: E402
    build_synthetic_corpus,
    capture_hardware,
    mean_reciprocal_rank,
    ndcg_at_k,
    recall_at_k,
)
from mneme_core.bench.hardware import write_hardware_json  # noqa: E402
from mneme_core.fts5.indexer import (  # noqa: E402
    IndexerConfig,
    connect,
    ensure_schema,
    index_vault,
)
from mneme_core.retrieval.rrf import (  # noqa: E402
    Hit,
    RetrievalConfig,
    fts5_search,
    retrieve,
)

DEFAULT_TOP_N = 10


def write_json(payload: object, output_path: Path) -> None:
    """Write benchmark JSON as UTF-8 without BOM on every platform."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def materialize_vault(corpus, vault_root: Path) -> None:
    """Write each synthetic doc as a markdown file under ``vault_root``."""
    vault_root.mkdir(parents=True, exist_ok=True)
    for doc in corpus.docs:
        path = vault_root / f"{doc.id}.md"
        body = (
            f"---\nid: {doc.id}\ntype: reference\ntopic: {doc.topic}\n---\n\n"
            f"# {doc.title}\n\n{doc.body}\n"
        )
        path.write_text(body, encoding="utf-8")


class BowBackend:
    """TF-cosine retrieval backend over the in-memory corpus.

    Implements the ``RetrievalBackend`` Protocol from
    :mod:`mneme_core.retrieval.rrf`. Pure Python, no numpy.

    This is not a substitute for a real dense embedding model. It is a
    deterministic second leg that lets the benchmark exercise RRF
    fusion code under controlled inputs.
    """

    def __init__(self, corpus) -> None:
        self._doc_vectors: list[tuple[str, Counter[str], float]] = []
        for doc in corpus.docs:
            tokens = (doc.title + " " + doc.body).lower().split()
            tf = Counter(tokens)
            norm = math.sqrt(sum(v * v for v in tf.values()))
            self._doc_vectors.append((doc.id, tf, norm))

    def __call__(self, query: str, limit: int) -> list[Hit]:
        q_tokens = query.lower().split()
        q_tf = Counter(q_tokens)
        q_norm = math.sqrt(sum(v * v for v in q_tf.values()))
        if q_norm == 0:
            return []
        scored: list[tuple[float, str]] = []
        for doc_id, tf, norm in self._doc_vectors:
            if norm == 0:
                continue
            dot = sum(q_tf[t] * tf.get(t, 0) for t in q_tf)
            if dot == 0:
                continue
            cosine = dot / (q_norm * norm)
            scored.append((cosine, doc_id))
        scored.sort(reverse=True)
        return [
            Hit(id=doc_id, path=f"{doc_id}.md", title="", score=score, source="bow")
            for score, doc_id in scored[:limit]
        ]


def _doc_id_from_hit(hit: Hit) -> str:
    """Recover the synthetic doc id from a hit's path."""
    if hit.path.endswith(".md"):
        return Path(hit.path).stem
    return str(hit.id)


def evaluate_condition(
    corpus,
    config: RetrievalConfig,
    dense_backend=None,
) -> dict[str, float]:
    """Run every query through ``retrieve`` and aggregate metrics."""
    ndcg_scores: list[float] = []
    recall_scores: list[float] = []
    rr_inputs: list[tuple[list[str], set[str]]] = []
    for q in corpus.queries:
        hits = retrieve(q.text, config, dense_backend=dense_backend)
        ranked_ids = [_doc_id_from_hit(h) for h in hits]
        relevant = set(q.relevant_doc_ids)
        ndcg_scores.append(ndcg_at_k(ranked_ids, relevant, k=5))
        recall_scores.append(recall_at_k(ranked_ids, relevant, k=10))
        rr_inputs.append((ranked_ids, relevant))
    return {
        "ndcg_at_5": (
            sum(ndcg_scores) / len(ndcg_scores) if ndcg_scores else 0.0
        ),
        "recall_at_10": (
            sum(recall_scores) / len(recall_scores) if recall_scores else 0.0
        ),
        "mrr": mean_reciprocal_rank(rr_inputs, cutoff=10),
        "queries_evaluated": len(corpus.queries),
    }


def evaluate_single_leg_fts5(corpus, db_path: Path) -> dict[str, float]:
    """FTS5-only baseline that skips RetrievalConfig.min_query_length.

    Calls ``fts5_search`` directly so the metric is comparable to the
    pipeline condition without being penalized by the short-query gate.
    """
    ndcg_scores: list[float] = []
    recall_scores: list[float] = []
    rr_inputs: list[tuple[list[str], set[str]]] = []
    for q in corpus.queries:
        hits = fts5_search(q.text, db_path, limit=20)
        ranked_ids = [_doc_id_from_hit(h) for h in hits]
        relevant = set(q.relevant_doc_ids)
        ndcg_scores.append(ndcg_at_k(ranked_ids, relevant, k=5))
        recall_scores.append(recall_at_k(ranked_ids, relevant, k=10))
        rr_inputs.append((ranked_ids, relevant))
    return {
        "ndcg_at_5": sum(ndcg_scores) / len(ndcg_scores) if ndcg_scores else 0.0,
        "recall_at_10": (
            sum(recall_scores) / len(recall_scores) if recall_scores else 0.0
        ),
        "mrr": mean_reciprocal_rank(rr_inputs, cutoff=10),
        "queries_evaluated": len(corpus.queries),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Benchmark A - retrieval quality")
    parser.add_argument(
        "--output-format",
        choices=("json", "table"),
        default="json",
    )
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument(
        "--docs-per-topic",
        type=int,
        default=50,
        help="Defaults match the plan headline (500 docs total).",
    )
    parser.add_argument(
        "--queries-per-topic",
        type=int,
        default=5,
        help="Defaults match the plan headline (50 queries total).",
    )
    parser.add_argument(
        "--top-k-per-backend",
        type=int,
        default=50,
        help="Backend per-leg cutoff before fusion.",
    )
    parser.add_argument(
        "--top-n-final",
        type=int,
        default=DEFAULT_TOP_N,
        help="Final cutoff returned by retrieve(); must be >= 10 for Recall@10.",
    )
    parser.add_argument(
        "--hardware-output",
        type=Path,
        default=None,
        help="Optional path to write hardware.json alongside the result.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Optional path to write JSON output as UTF-8 without BOM.",
    )
    args = parser.parse_args()

    corpus = build_synthetic_corpus(
        seed=args.seed,
        docs_per_topic=args.docs_per_topic,
        queries_per_topic=args.queries_per_topic,
    )

    with tempfile.TemporaryDirectory(prefix="mneme-bench-retrieval-") as tmp:
        tmp_path = Path(tmp)
        vault_root = tmp_path / "vault"
        db_path = tmp_path / ".mneme" / "fts5.sqlite"
        materialize_vault(corpus, vault_root)

        conn = connect(db_path)
        try:
            ensure_schema(conn)
            stats = index_vault(
                conn,
                IndexerConfig(vault_root=vault_root, db_path=db_path),
            )
        finally:
            conn.close()

        # ``min_query_length`` defaults to 20; the synthetic queries are
        # padded to exceed it (see test_bench_synth.py).
        retrieval_config = RetrievalConfig(
            fts5_db=db_path,
            top_k_per_backend=args.top_k_per_backend,
            top_n_final=max(args.top_n_final, 10),
        )

        baseline_fts5 = evaluate_single_leg_fts5(corpus, db_path)
        pipeline_fts5_only = evaluate_condition(corpus, retrieval_config)

        bow_backend = BowBackend(corpus)
        fused = evaluate_condition(
            corpus,
            retrieval_config,
            dense_backend=bow_backend,
        )

    payload = {
        "benchmark": "retrieval-quality",
        "seed": args.seed,
        "corpus": {
            "docs": len(corpus.docs),
            "queries": len(corpus.queries),
            "topics": corpus.topics,
            "indexer_stats": {
                "indexed": stats.indexed,
                "skipped_excluded": stats.skipped_excluded,
                "skipped_error": stats.skipped_error,
                "total_seen": stats.total_seen,
            },
        },
        "conditions": {
            "baseline_fts5_only": baseline_fts5,
            "pipeline_fts5_only": pipeline_fts5_only,
            "pipeline_rrf_fts5_plus_bow": fused,
        },
        "headline_ndcg_at_5": fused["ndcg_at_5"],
    }

    if args.hardware_output is not None:
        write_hardware_json(capture_hardware(seed=args.seed), args.hardware_output)

    if args.output is not None:
        write_json(payload, args.output)

    if args.output_format == "json" and args.output is None:
        json.dump(payload, sys.stdout, indent=2)
        sys.stdout.write("\n")
    elif args.output_format == "table":
        sys.stdout.write("Benchmark A - retrieval quality\n")
        for condition, metrics in payload["conditions"].items():
            sys.stdout.write(f"  {condition}:\n")
            for key, value in metrics.items():
                sys.stdout.write(f"    {key}: {value}\n")

    return 0


if __name__ == "__main__":
    sys.exit(main())
