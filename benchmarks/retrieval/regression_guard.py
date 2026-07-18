"""CI regression guard for Benchmark A - retrieval quality.

Checks three things against the locked baseline in ``baseline.json``:

1. ``conditions.pipeline_fts5_only.ndcg_at_5`` must not drop
   by more than ``--threshold`` (default 0.02).
2. ``conditions.pipeline_fts5_only.recall_at_10`` must not drop
   by more than ``--recall-threshold`` (default 0.05).
3. ``negative_probe.all_passed`` in the fresh result must be ``true``.

Override the primary metric path with ``--metric-path`` if a different
condition becomes the headline in a future revision.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def load_json(path: Path) -> object:
    """Load JSON written by Python, UTF-8 BOM tools, or PowerShell redirect."""
    raw = path.read_bytes()
    last_error: Exception | None = None
    for encoding in ("utf-8-sig", "utf-16"):
        try:
            return json.loads(raw.decode(encoding))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            last_error = exc
    if last_error is not None:
        raise last_error
    raise ValueError(f"empty JSON path: {path}")


def fetch_dotted(obj: object, dotted: str) -> float:
    cur: object = obj
    for part in dotted.split("."):
        if not isinstance(cur, dict):
            raise KeyError(f"Path segment {part!r} hit a non-dict in {dotted}")
        cur = cur[part]
    if not isinstance(cur, int | float):
        raise TypeError(f"Resolved {dotted} to a non-numeric: {cur!r}")
    return float(cur)


def fetch_bool(obj: object, dotted: str) -> bool:
    cur: object = obj
    for part in dotted.split("."):
        if not isinstance(cur, dict):
            raise KeyError(f"Path segment {part!r} hit a non-dict in {dotted}")
        cur = cur[part]
    if not isinstance(cur, bool):
        raise TypeError(f"Resolved {dotted} to a non-bool: {cur!r}")
    return cur


def main() -> int:
    parser = argparse.ArgumentParser(description="Regression guard for Bench A.")
    parser.add_argument("result", type=Path, help="Fresh result.json to check.")
    parser.add_argument(
        "--baseline",
        type=Path,
        default=Path(__file__).resolve().parent / "baseline.json",
        help="Locked baseline to compare against.",
    )
    parser.add_argument(
        "--metric-path",
        type=str,
        default="conditions.pipeline_fts5_only.ndcg_at_5",
    )
    parser.add_argument(
        "--threshold",
        type=float,
        default=0.02,
        help="Allowed absolute drop for the primary metric (nDCG@5).",
    )
    parser.add_argument(
        "--recall-metric-path",
        type=str,
        default="conditions.pipeline_fts5_only.recall_at_10",
    )
    parser.add_argument(
        "--recall-threshold",
        type=float,
        default=0.05,
        help="Allowed absolute drop for Recall@10.",
    )
    args = parser.parse_args()

    result = load_json(args.result)
    baseline = load_json(args.baseline)

    # --- nDCG@5 check ---
    fresh_ndcg = fetch_dotted(result, args.metric_path)
    locked_ndcg = fetch_dotted(baseline, args.metric_path)
    ndcg_drop = locked_ndcg - fresh_ndcg
    ndcg_passed = ndcg_drop <= args.threshold

    # --- Recall@10 check ---
    fresh_recall = fetch_dotted(result, args.recall_metric_path)
    locked_recall = fetch_dotted(baseline, args.recall_metric_path)
    recall_drop = locked_recall - fresh_recall
    recall_passed = recall_drop <= args.recall_threshold

    # --- Negative probe check ---
    # The field may be absent in result files produced before this guard
    # was introduced; treat absence as a failure so old results do not
    # silently skip the check.
    probe_passed: bool
    if isinstance(result, dict) and "negative_probe" in result:
        probe_passed = fetch_bool(result, "negative_probe.all_passed")
    else:
        probe_passed = False

    overall_passed = ndcg_passed and recall_passed and probe_passed

    report = {
        "ndcg_at_5": {
            "metric": args.metric_path,
            "baseline": locked_ndcg,
            "result": fresh_ndcg,
            "absolute_drop": ndcg_drop,
            "threshold": args.threshold,
            "passed": ndcg_passed,
        },
        "recall_at_10": {
            "metric": args.recall_metric_path,
            "baseline": locked_recall,
            "result": fresh_recall,
            "absolute_drop": recall_drop,
            "threshold": args.recall_threshold,
            "passed": recall_passed,
        },
        "negative_probe": {
            "passed": probe_passed,
        },
        "overall_passed": overall_passed,
    }
    json.dump(report, sys.stdout, indent=2)
    sys.stdout.write("\n")
    return 0 if overall_passed else 1


if __name__ == "__main__":
    sys.exit(main())
