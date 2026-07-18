"""Benchmark A - Retrieval Quality.

Builds the 500-doc synthetic corpus, indexes it with the production
FTS5 indexer, then runs every synthetic query through the production
``retrieve`` pipeline. Reports nDCG@5, Recall@10, and MRR aggregated
across the query set.

A second condition exercises RRF fusion over two backends:

* FTS5 BM25 (always)
* a pure-Python BoW cosine surrogate that stands in for the dense
  embedding backend the standard profile will ship post-v1.0

The BoW backend is intentionally synthetic. It exercises the production RRF
fusion code path as an ablation. The production FTS5-only path remains the
quality headline because the surrogate is not a shipped semantic backend.

Negative probe
--------------
A small set of noisy queries is also run.  These queries contain only
generic filler words that do NOT appear in any document title or body
vocabulary.  The criterion: every hit returned for a negative-probe
query must have a BM25/cosine score below a low-confidence ceiling
(operationalised as: the top hit's raw score must be lower than the
minimum top-hit score observed across the positive queries, OR the
result list is empty).  Because the synthetic corpus uses a closed
vocabulary, a query built exclusively from out-of-vocabulary terms
should produce no results at all from the FTS5 leg; the probe verifies
that the pipeline does not hallucinate relevance.

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
from collections.abc import Mapping
from pathlib import Path

# Allow running from the repo root without installing the package.
_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_REPO_ROOT / "packages" / "mneme-core" / "src"))

from mneme_core.bench import (  # noqa: E402
    SyntheticCorpus,
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


def materialize_vault(corpus: SyntheticCorpus, vault_root: Path) -> None:
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

    def __init__(
        self,
        corpus: SyntheticCorpus,
        canonical_ids: Mapping[str, int],
    ) -> None:
        self._doc_vectors: list[tuple[int, str, Counter[str], float]] = []
        for doc in corpus.docs:
            canonical_id = canonical_ids.get(doc.id)
            if canonical_id is None:
                raise ValueError(f"missing FTS5 identity for synthetic document {doc.id}")
            tokens = (doc.title + " " + doc.body).lower().split()
            tf = Counter(tokens)
            norm = math.sqrt(sum(v * v for v in tf.values()))
            self._doc_vectors.append((canonical_id, doc.id, tf, norm))

    def __call__(self, query: str, limit: int) -> list[Hit]:
        q_tokens = query.lower().split()
        q_tf = Counter(q_tokens)
        q_norm = math.sqrt(sum(v * v for v in q_tf.values()))
        if q_norm == 0:
            return []
        scored: list[tuple[float, int, str]] = []
        for canonical_id, doc_id, tf, norm in self._doc_vectors:
            if norm == 0:
                continue
            dot = sum(q_tf[t] * tf.get(t, 0) for t in q_tf)
            if dot == 0:
                continue
            cosine = dot / (q_norm * norm)
            scored.append((cosine, canonical_id, doc_id))
        scored.sort(reverse=True)
        return [
            Hit(
                id=canonical_id,
                path=f"{doc_id}.md",
                title="",
                score=score,
                source="bow",
            )
            for score, canonical_id, doc_id in scored[:limit]
        ]


def _doc_id_from_hit(hit: Hit) -> str:
    """Recover the synthetic doc id from a hit's path."""
    if hit.path.endswith(".md"):
        return Path(hit.path).stem
    return str(hit.id)


def evaluate_condition(
    corpus: SyntheticCorpus,
    config: RetrievalConfig,
    dense_backend: BowBackend | None = None,
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


def evaluate_single_leg_fts5(corpus: SyntheticCorpus, db_path: Path) -> dict[str, float]:
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


# ---------------------------------------------------------------------------
# Negative probe
# ---------------------------------------------------------------------------

# These query strings contain ONLY generic English words that are absent from
# every topic vocabulary in synth.py (_TOPIC_VOCAB) and from the _FILLER
# list.  They are chosen so the FTS5 index returns an empty result set; any
# non-empty result would indicate the retriever is matching on stop-word noise.
#
# The probe is defined statically here and must NOT be derived from the
# system's output.  They were authored before running the harness and are
# frozen as part of the pre-registration commit (see PROTOCOL.md).
_NEGATIVE_PROBE_QUERIES: list[str] = [
    "luminescent crystallography photon refraction prism",
    "seismic tectonic lithosphere mantle subduction",
    "endoplasmic reticulum mitochondria ribosome cytoplasm",
    "sonata allegro cadence fugue counterpoint harmony",
    "hydraulic reservoir turbine impeller cavitation nozzle",
]

# Criterion: a negative-probe query passes if the FTS5 leg returns NO hits.
# The FTS5 index only stores tokens it has seen; out-of-vocabulary query terms
# produce an empty match set.  A non-empty result means the retriever matched
# on partial token overlap with stop-words or index noise, which is a defect.
_NEGATIVE_PROBE_MAX_HITS = 0


def evaluate_negative_probe(db_path: Path) -> dict[str, object]:
    """Run the negative-probe queries and report pass/fail per query.

    Pass criterion: FTS5 returns zero hits for every probe query.
    Any non-zero result set is a failure (hallucinated relevance).
    """
    results: list[dict[str, object]] = []
    all_passed = True
    for query in _NEGATIVE_PROBE_QUERIES:
        hits = fts5_search(query, db_path, limit=5)
        hit_count = len(hits)
        passed = hit_count <= _NEGATIVE_PROBE_MAX_HITS
        if not passed:
            all_passed = False
        results.append(
            {
                "query": query,
                "hits_returned": hit_count,
                "max_hits_allowed": _NEGATIVE_PROBE_MAX_HITS,
                "passed": passed,
            }
        )
    return {
        "criterion": (
            f"FTS5 must return <= {_NEGATIVE_PROBE_MAX_HITS} hits "
            "for every out-of-vocabulary query"
        ),
        "queries_probed": len(_NEGATIVE_PROBE_QUERIES),
        "all_passed": all_passed,
        "per_query": results,
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
            canonical_ids = {
                Path(str(path)).stem: int(doc_id)
                for doc_id, path in conn.execute("SELECT id, path FROM documents")
            }
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

        bow_backend = BowBackend(corpus, canonical_ids)
        fused = evaluate_condition(
            corpus,
            retrieval_config,
            dense_backend=bow_backend,
        )

        negative_probe = evaluate_negative_probe(db_path)

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
        "headline_condition": "pipeline_fts5_only",
        "headline_ndcg_at_5": pipeline_fts5_only["ndcg_at_5"],
        "headline_recall_at_10": pipeline_fts5_only["recall_at_10"],
        "rrf_bow_ablation": {
            "classification": "lexical-surrogate-not-semantic-model",
            "ndcg_at_5_delta_vs_production": (
                fused["ndcg_at_5"] - pipeline_fts5_only["ndcg_at_5"]
            ),
            "recall_at_10_delta_vs_production": (
                fused["recall_at_10"] - pipeline_fts5_only["recall_at_10"]
            ),
        },
        "negative_probe": negative_probe,
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
        sys.stdout.write(
            f"  headline_ndcg_at_5: {payload['headline_ndcg_at_5']}\n"
        )
        sys.stdout.write(
            f"  headline_recall_at_10: {payload['headline_recall_at_10']}\n"
        )
        probe = payload["negative_probe"]
        sys.stdout.write(
            f"  negative_probe: all_passed={probe['all_passed']} "
            f"({probe['queries_probed']} queries)\n"
        )

    return 0


if __name__ == "__main__":
    sys.exit(main())
