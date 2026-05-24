"""Benchmark B - Latency distribution.

Measures three latency-critical operations:

1. **Stop-hook proxy** (``atomic_write_text`` of a representative
   markdown record). The plan's Zero-LLM-Stop claim translates to
   `p95 < 1000ms` on commodity hardware - we own the constant.
2. **Retrieve**: end-to-end ``retrieve`` call over an indexed synthetic
   corpus. Distribution covers warm-cache behavior with the database
   re-opened per query (the production access pattern).
3. **Indexer throughput**: full ``index_vault`` pass at corpus sizes
   1k / 3k / 7k documents to expose scaling behavior.

The raw sample arrays are kept out of the JSON so result files stay
small; only the percentile sketch and sample count survive.
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
import time
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_REPO_ROOT / "packages" / "mneme-core" / "src"))

from mneme_core.bench import (  # noqa: E402
    build_synthetic_corpus,
    capture_hardware,
    percentiles,
)
from mneme_core.bench.hardware import write_hardware_json  # noqa: E402
from mneme_core.fts5.indexer import (  # noqa: E402
    IndexerConfig,
    connect,
    ensure_schema,
    index_vault,
)
from mneme_core.retrieval.rrf import RetrievalConfig, retrieve  # noqa: E402
from mneme_core.vault.atomic_write import atomic_write_text  # noqa: E402

DEFAULT_QUANTILES = [0.5, 0.95, 0.99]


def write_json(payload: object, output_path: Path) -> None:
    """Write benchmark JSON as UTF-8 without BOM on every platform."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def _materialize_vault(corpus, vault_root: Path) -> None:
    vault_root.mkdir(parents=True, exist_ok=True)
    for doc in corpus.docs:
        path = vault_root / f"{doc.id}.md"
        path.write_text(
            f"---\nid: {doc.id}\ntype: reference\n---\n\n"
            f"# {doc.title}\n\n{doc.body}\n",
            encoding="utf-8",
        )


def _build_index(vault_root: Path, db_path: Path) -> None:
    conn = connect(db_path)
    try:
        ensure_schema(conn)
        index_vault(conn, IndexerConfig(vault_root=vault_root, db_path=db_path))
    finally:
        conn.close()


def measure_stop_hook_proxy(sessions: int, tmp_root: Path) -> dict[str, float | int]:
    """Time the atomic-write that the Stop hook performs per session.

    Generates a deterministic markdown record per session and timeses
    the write to a fresh path. ``atomic_write_text`` is the same
    primitive the plugin uses, so this is a representative proxy for
    Stop-hook critical-path latency.
    """
    target_dir = tmp_root / "sessions"
    target_dir.mkdir(parents=True, exist_ok=True)
    samples_ms: list[float] = []
    body_template = (
        "---\nid: session-{i}\ntype: session\ncreated: '2026-05-19T00:00:00Z'\n"
        "schema_version: 1\n---\n\n"
        "# Session {i}\n\n"
        "## Summary\n\nDeterministic stop-hook payload. {filler}\n"
    )
    filler = " ".join(["frame"] * 40)
    for i in range(sessions):
        body = body_template.format(i=i, filler=filler)
        target = target_dir / f"session-{i:04d}.md"
        t0 = time.perf_counter()
        atomic_write_text(target, body)
        samples_ms.append((time.perf_counter() - t0) * 1000.0)
    return {
        "samples": len(samples_ms),
        **percentiles(samples_ms, DEFAULT_QUANTILES),
    }


def measure_retrieve(queries: int, tmp_root: Path) -> dict[str, float | int]:
    """Time end-to-end ``retrieve`` calls over an indexed corpus.

    The corpus is materialized once and indexed once; only the
    retrieval call is in the timed loop. Queries cycle through the
    synthetic query set until the sample target is met.
    """
    corpus = build_synthetic_corpus(docs_per_topic=30, queries_per_topic=5)
    vault_root = tmp_root / "retrieve-vault"
    db_path = tmp_root / "retrieve-vault" / ".mneme" / "fts5.sqlite"
    _materialize_vault(corpus, vault_root)
    _build_index(vault_root, db_path)

    cfg = RetrievalConfig(fts5_db=db_path, top_n_final=10)
    samples_ms: list[float] = []
    q_texts = [q.text for q in corpus.queries]
    if not q_texts:
        return {"samples": 0, **percentiles([], DEFAULT_QUANTILES)}
    for i in range(queries):
        q = q_texts[i % len(q_texts)]
        t0 = time.perf_counter()
        retrieve(q, cfg)
        samples_ms.append((time.perf_counter() - t0) * 1000.0)
    return {
        "samples": len(samples_ms),
        **percentiles(samples_ms, DEFAULT_QUANTILES),
    }


def measure_indexer_scaling(tmp_root: Path) -> list[dict[str, float | int]]:
    """Index ``index_vault`` at 1k / 3k / 7k synthetic docs.

    Returns one record per scale point with wall time, docs indexed,
    and derived docs-per-second throughput. Each scale point materializes
    its own vault and database to avoid warm-cache bleed.
    """
    out: list[dict[str, float | int]] = []
    # 10 topics x N docs_per_topic = N * 10 docs.
    scale_points = [(100, "1k"), (300, "3k"), (700, "7k")]
    for docs_per_topic, label in scale_points:
        corpus = build_synthetic_corpus(
            docs_per_topic=docs_per_topic,
            queries_per_topic=1,
        )
        vault_root = tmp_root / f"indexer-{label}-vault"
        db_path = vault_root / ".mneme" / "fts5.sqlite"
        _materialize_vault(corpus, vault_root)

        conn = connect(db_path)
        try:
            ensure_schema(conn)
            t0 = time.perf_counter()
            stats = index_vault(
                conn,
                IndexerConfig(vault_root=vault_root, db_path=db_path),
            )
            elapsed_s = time.perf_counter() - t0
        finally:
            conn.close()

        out.append(
            {
                "scale": label,
                "docs_indexed": stats.indexed,
                "elapsed_seconds": round(elapsed_s, 3),
                "docs_per_second": (
                    round(stats.indexed / elapsed_s, 1) if elapsed_s > 0 else 0
                ),
            }
        )
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description="Benchmark B - latency")
    parser.add_argument(
        "--output-format",
        choices=("json", "table"),
        default="json",
    )
    parser.add_argument(
        "--sessions",
        type=int,
        default=100,
        help="Stop-hook proxy sample count.",
    )
    parser.add_argument(
        "--queries",
        type=int,
        default=1000,
        help="Retrieval sample count.",
    )
    parser.add_argument(
        "--skip-indexer-scaling",
        action="store_true",
        help="Skip the 1k/3k/7k indexer sweep (it is the slowest leg).",
    )
    parser.add_argument(
        "--hardware-output",
        type=Path,
        default=None,
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Optional path to write JSON output as UTF-8 without BOM.",
    )
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()

    with tempfile.TemporaryDirectory(prefix="mneme-bench-latency-") as tmp:
        tmp_root = Path(tmp)
        stop_hook = measure_stop_hook_proxy(args.sessions, tmp_root)
        retrieve_dist = measure_retrieve(args.queries, tmp_root)
        indexer = (
            [] if args.skip_indexer_scaling else measure_indexer_scaling(tmp_root)
        )

    payload = {
        "benchmark": "latency",
        "seed": args.seed,
        "stop_hook_proxy_ms": stop_hook,
        "retrieve_ms": retrieve_dist,
        "indexer_scaling": indexer,
    }

    if args.hardware_output is not None:
        write_hardware_json(capture_hardware(seed=args.seed), args.hardware_output)

    if args.output is not None:
        write_json(payload, args.output)

    if args.output_format == "json" and args.output is None:
        json.dump(payload, sys.stdout, indent=2)
        sys.stdout.write("\n")
    elif args.output_format == "table":
        sys.stdout.write("Benchmark B - latency\n")
        sys.stdout.write(f"  stop_hook_proxy_ms p95: {stop_hook.get('p95')}\n")
        sys.stdout.write(f"  retrieve_ms p95:        {retrieve_dist.get('p95')}\n")
        for record in indexer:
            sys.stdout.write(
                f"  indexer {record['scale']}: "
                f"{record['docs_per_second']} docs/s\n"
            )
    return 0


if __name__ == "__main__":
    sys.exit(main())
