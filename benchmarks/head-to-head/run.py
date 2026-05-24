"""Benchmark E - Head-to-head retrieval comparison.

Runs identical query sets through every available memory adapter and
reports nDCG@5, Recall@10, MRR, and elapsed time per adapter so the
v1.0 launch can publish honest head-to-head numbers.

The fixture is the same synthetic corpus the rest of the bench suite
uses (``mneme_core.bench.synth``), repackaged into a claude-mem
SQLite shape so both adapters can ingest it. Each synthetic document
becomes one observation; the corpus's title-anchored relevance
judgments carry over by mapping each ``relevant_doc_ids`` entry to
the corresponding ``cm-obs-{i}`` migrated filename.

ClaudeMemAdapter is a stub by default; the harness reports its
condition as ``unavailable`` until the operator installs claude-mem
locally and sets ``CLAUDE_MEM_BIN``. MnemeAdapter runs unconditionally
when ``npx`` is available.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import tempfile
import time
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_REPO_ROOT / "packages" / "mneme-core" / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from mneme_core.bench import (  # noqa: E402
    build_synthetic_corpus,
    capture_hardware,
    mean_reciprocal_rank,
    ndcg_at_k,
    recall_at_k,
)
from mneme_core.bench.hardware import write_hardware_json  # noqa: E402
from adapters import (  # noqa: E402
    AdapterStatus,
    ClaudeMemAdapter,
    MemoryAdapter,
    MnemeAdapter,
)


def build_claude_mem_fixture(corpus, path: Path) -> int:
    """Repackage the synthetic corpus into a claude-mem schema."""
    conn = sqlite3.connect(path)
    try:
        conn.execute(
            """CREATE TABLE observations (
                 id INTEGER PRIMARY KEY,
                 memory_session_id TEXT,
                 project TEXT,
                 text TEXT,
                 type TEXT,
                 title TEXT,
                 subtitle TEXT,
                 facts TEXT,
                 narrative TEXT,
                 concepts TEXT,
                 files_read TEXT,
                 files_modified TEXT,
                 prompt_number INTEGER,
                 discovery_tokens INTEGER,
                 created_at TEXT,
                 created_at_epoch INTEGER,
                 content_hash TEXT,
                 generated_by_model TEXT,
                 agent_type TEXT,
                 agent_id TEXT,
                 metadata TEXT
               )"""
        )
        conn.execute(
            """CREATE TABLE session_summaries (
                 id INTEGER PRIMARY KEY,
                 memory_session_id TEXT,
                 summary TEXT,
                 created_at TEXT
               )"""
        )
        conn.execute(
            """CREATE TABLE user_prompts (
                 id INTEGER PRIMARY KEY,
                 memory_session_id TEXT,
                 prompt_text TEXT,
                 created_at TEXT
               )"""
        )
        for i, doc in enumerate(corpus.docs, start=1):
            day = ((i - 1) % 28) + 1
            created_at = f"2026-04-{day:02d}T08:00:00Z"
            epoch = 1_775_376_000 + i * 60
            conn.execute(
                """INSERT INTO observations VALUES (
                     ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
                   )""",
                (
                    i,
                    f"session-{(i % 5):02d}",
                    "head-to-head-fixture",
                    "",
                    doc.topic,
                    doc.title,
                    "",
                    "",
                    doc.body,
                    "",
                    "[]",
                    "[]",
                    i,
                    100,
                    created_at,
                    epoch,
                    f"synth-{i}",
                    "synthetic-model",
                    "main",
                    f"agent-{i % 3}",
                    "{}",
                ),
            )
        conn.commit()
        return len(corpus.docs)
    finally:
        conn.close()


def doc_id_to_migrated_filename(_doc_id: str, row_index_one_based: int) -> str:
    """Convert a synthetic doc id to its post-migration filename stem."""
    return f"cm-obs-{row_index_one_based}"


def build_relevance(corpus) -> dict[str, set[str]]:
    """Map ``query.qid -> set of migrated filename stems``."""
    id_to_row: dict[str, int] = {
        doc.id: idx for idx, doc in enumerate(corpus.docs, start=1)
    }
    out: dict[str, set[str]] = {}
    for q in corpus.queries:
        out[q.qid] = {
            doc_id_to_migrated_filename(d, id_to_row[d]) for d in q.relevant_doc_ids
        }
    return out


def evaluate_adapter(
    adapter: MemoryAdapter,
    corpus,
    relevance: dict[str, set[str]],
    workdir: Path,
    source_db: Path,
) -> dict[str, object]:
    """Run migration + queries through one adapter, return aggregate metrics."""
    status = adapter.status()
    if not status.available:
        return {
            "adapter": status.name,
            "available": False,
            "reason": status.reason,
        }

    t0 = time.perf_counter()
    migrate_result = adapter.migrate(source_db, workdir)
    migrate_elapsed = time.perf_counter() - t0
    if isinstance(migrate_result, dict) and migrate_result.get("status") == "error":
        return {
            "adapter": status.name,
            "available": True,
            "migrate_status": "error",
            "migrate_detail": migrate_result,
        }

    ndcg_scores: list[float] = []
    recall_scores: list[float] = []
    rr_inputs: list[tuple[list[str], set[str]]] = []
    query_latencies: list[float] = []
    for q in corpus.queries:
        rel = relevance.get(q.qid, set())
        t1 = time.perf_counter()
        hits = adapter.search(q.text, top_k=10)
        query_latencies.append((time.perf_counter() - t1) * 1000.0)
        ranked = [h.doc_id for h in hits]
        ndcg_scores.append(ndcg_at_k(ranked, rel, k=5))
        recall_scores.append(recall_at_k(ranked, rel, k=10))
        rr_inputs.append((ranked, rel))

    avg_query_ms = (
        sum(query_latencies) / len(query_latencies) if query_latencies else 0.0
    )

    return {
        "adapter": status.name,
        "available": True,
        "migrate_status": "ok",
        "migrate_elapsed_seconds": round(migrate_elapsed, 3),
        "queries_evaluated": len(corpus.queries),
        "metrics": {
            "ndcg_at_5": (
                sum(ndcg_scores) / len(ndcg_scores) if ndcg_scores else 0.0
            ),
            "recall_at_10": (
                sum(recall_scores) / len(recall_scores) if recall_scores else 0.0
            ),
            "mrr": mean_reciprocal_rank(rr_inputs, cutoff=10),
        },
        "avg_query_latency_ms": round(avg_query_ms, 3),
    }


def summarize(per_adapter: list[dict[str, object]]) -> dict[str, object]:
    """Build a side-by-side comparison summary."""
    available = [a for a in per_adapter if a.get("available")]
    if not available:
        return {"comparable": False, "reason": "no adapter was available"}
    return {
        "comparable": True,
        "adapters_compared": [a.get("adapter") for a in available],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Benchmark E - head-to-head")
    parser.add_argument(
        "--output-format",
        choices=("json", "table"),
        default="json",
    )
    parser.add_argument("--docs-per-topic", type=int, default=30)
    parser.add_argument("--queries-per-topic", type=int, default=3)
    parser.add_argument(
        "--hardware-output",
        type=Path,
        default=None,
    )
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()

    corpus = build_synthetic_corpus(
        seed=args.seed,
        docs_per_topic=args.docs_per_topic,
        queries_per_topic=args.queries_per_topic,
    )
    relevance = build_relevance(corpus)

    if args.hardware_output is not None:
        write_hardware_json(capture_hardware(seed=args.seed), args.hardware_output)

    adapters: list[MemoryAdapter] = [MnemeAdapter(), ClaudeMemAdapter()]
    statuses: list[AdapterStatus] = [a.status() for a in adapters]

    with tempfile.TemporaryDirectory(prefix="mneme-bench-h2h-") as tmp:
        tmp_root = Path(tmp)
        source_db = tmp_root / "claude-mem.db"
        fixture_rows = build_claude_mem_fixture(corpus, source_db)

        results: list[dict[str, object]] = []
        for adapter in adapters:
            results.append(
                evaluate_adapter(
                    adapter,
                    corpus,
                    relevance,
                    workdir=tmp_root,
                    source_db=source_db,
                )
            )

    payload = {
        "benchmark": "head-to-head",
        "seed": args.seed,
        "fixture": {
            "docs": len(corpus.docs),
            "queries": len(corpus.queries),
            "fixture_rows": fixture_rows,
        },
        "adapter_status": [
            {"name": s.name, "available": s.available, "reason": s.reason}
            for s in statuses
        ],
        "results": results,
        "summary": summarize(results),
    }

    if args.output_format == "json":
        json.dump(payload, sys.stdout, indent=2)
        sys.stdout.write("\n")
    else:
        sys.stdout.write("Benchmark E - head-to-head\n")
        for r in results:
            if r.get("available"):
                metrics = r.get("metrics", {})
                sys.stdout.write(
                    f"  {r['adapter']}: nDCG@5={metrics.get('ndcg_at_5')}, "
                    f"MRR={metrics.get('mrr')}\n"
                )
            else:
                sys.stdout.write(
                    f"  {r.get('adapter')}: unavailable ({r.get('reason')})\n"
                )
    return 0


if __name__ == "__main__":
    sys.exit(main())
