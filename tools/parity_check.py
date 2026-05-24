"""CLI entry point for the parity-check harness.

Usage::

    python tools/parity_check.py \\
        --vault ~/mneme-vault \\
        --ground-truth tools.parity.my_local_adapter:MyAdapter \\
        --queries queries.json \\
        --output parity-report.json

The ground-truth adapter is loaded from a Python module path. The
operator's adapter lives outside the mneme tree (typically in their
own vault directory) and conforms to :class:`ParityAdapter`. The
harness then drives both systems through the query set and aggregates
parity metrics.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT))

from tools.parity.adapter import (  # noqa: E402
    MnemeParityAdapter,
    load_adapter_by_path,
)
from tools.parity.harness import (  # noqa: E402
    load_queries_from_json,
    run_parity_check,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Compare mneme retrieval against a ground-truth memory "
            "system on a real query set. Exit 0 if the parity gate "
            "passes, 1 otherwise."
        )
    )
    parser.add_argument(
        "--vault",
        type=Path,
        required=True,
        help="Path to the mneme vault directory.",
    )
    parser.add_argument(
        "--db",
        type=Path,
        default=None,
        help="Override the FTS5 index path (default: <vault>/.mneme/fts5.sqlite).",
    )
    parser.add_argument(
        "--ground-truth",
        required=True,
        help=(
            "Python import path to the ground-truth adapter, shaped "
            "'module.path:ClassName'. The class is instantiated with no "
            "arguments."
        ),
    )
    parser.add_argument(
        "--queries",
        type=Path,
        required=True,
        help="JSON file: [{\"id\": str, \"text\": str}, ...]",
    )
    parser.add_argument(
        "--top-k",
        type=int,
        default=10,
        help="Top-k per adapter per query (default: 10).",
    )
    parser.add_argument(
        "--divergence-threshold",
        type=float,
        default=0.6,
        help="top-5 Jaccard below this counts as divergent (default: 0.6).",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Write JSON report to this path instead of stdout.",
    )
    args = parser.parse_args(argv)

    mneme = MnemeParityAdapter(vault=args.vault, db_path=args.db)
    ground_truth = load_adapter_by_path(args.ground_truth)
    queries = load_queries_from_json(args.queries)

    report = run_parity_check(
        mneme=mneme,
        ground_truth=ground_truth,
        queries=queries,
        top_k=args.top_k,
        divergence_threshold=args.divergence_threshold,
    )

    output_text = json.dumps(report, indent=2, default=str)
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(output_text, encoding="utf-8")
    else:
        sys.stdout.write(output_text)
        sys.stdout.write("\n")

    summary = report.get("summary", {})
    if isinstance(summary, dict) and not summary.get("gate_passed", False):
        return 1
    if not report.get("comparable", False):
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
