"""CI regression guard for Benchmark A - retrieval quality.

Compares the headline metric from a fresh ``result.json`` against the
locked baseline in ``baseline.json`` and exits non-zero if the metric
drops by more than ``--threshold`` (default 0.02).

Default metric is ``conditions.pipeline_rrf_fts5_plus_bow.ndcg_at_5``,
the production pipeline scored on the synthetic corpus with RRF
fusion enabled. Override with ``--metric-path`` if a different
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
        default="conditions.pipeline_rrf_fts5_plus_bow.ndcg_at_5",
    )
    parser.add_argument(
        "--threshold",
        type=float,
        default=0.02,
        help="Allowed absolute drop before the guard fails.",
    )
    args = parser.parse_args()

    result = load_json(args.result)
    baseline = load_json(args.baseline)
    fresh = fetch_dotted(result, args.metric_path)
    locked = fetch_dotted(baseline, args.metric_path)
    drop = locked - fresh

    report = {
        "metric": args.metric_path,
        "baseline": locked,
        "result": fresh,
        "absolute_drop": drop,
        "threshold": args.threshold,
        "passed": drop <= args.threshold,
    }
    json.dump(report, sys.stdout, indent=2)
    sys.stdout.write("\n")
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
