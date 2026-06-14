"""CI regression guard for Benchmark F - Compaction Recall.

Checks three things against the locked baseline in ``baseline.json``:

1. ``conditions.mneme_selfheal.recall_at_k`` must not drop by more than
   ``--threshold`` (default 0.05).
2. ``headline_recall_gain`` must not drop by more than
   ``--gain-threshold`` (default 0.05).
3. ``negative_probe.all_passed`` in the fresh result must be ``true``
   (self-heal must never invent a fact not in the original checkpoint).

Override the primary metric path with ``--metric-path`` if the condition
name changes in a future revision.
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
    parser = argparse.ArgumentParser(description="Regression guard for Bench F.")
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
        default="conditions.mneme_selfheal.recall_at_k",
        help="Dotted path to the primary recall metric.",
    )
    parser.add_argument(
        "--threshold",
        type=float,
        default=0.05,
        help="Allowed absolute drop for the primary recall metric.",
    )
    parser.add_argument(
        "--gain-metric-path",
        type=str,
        default="headline_recall_gain",
        help="Dotted path to the headline recall gain metric.",
    )
    parser.add_argument(
        "--gain-threshold",
        type=float,
        default=0.05,
        help="Allowed absolute drop for headline_recall_gain.",
    )
    args = parser.parse_args()

    result = load_json(args.result)
    baseline = load_json(args.baseline)

    # --- Primary recall check ---
    fresh_recall = fetch_dotted(result, args.metric_path)
    locked_recall = fetch_dotted(baseline, args.metric_path)
    recall_drop = locked_recall - fresh_recall
    recall_passed = recall_drop <= args.threshold

    # --- Headline gain check ---
    fresh_gain = fetch_dotted(result, args.gain_metric_path)
    locked_gain = fetch_dotted(baseline, args.gain_metric_path)
    gain_drop = locked_gain - fresh_gain
    gain_passed = gain_drop <= args.gain_threshold

    # --- Negative probe check ---
    # Treat absence as failure so old results do not silently skip the check.
    probe_passed: bool
    if isinstance(result, dict) and "negative_probe" in result:
        probe_passed = fetch_bool(result, "negative_probe.all_passed")
    else:
        probe_passed = False

    overall_passed = recall_passed and gain_passed and probe_passed

    report = {
        "mneme_selfheal_recall_at_k": {
            "metric": args.metric_path,
            "baseline": locked_recall,
            "result": fresh_recall,
            "absolute_drop": recall_drop,
            "threshold": args.threshold,
            "passed": recall_passed,
        },
        "headline_recall_gain": {
            "metric": args.gain_metric_path,
            "baseline": locked_gain,
            "result": fresh_gain,
            "absolute_drop": gain_drop,
            "threshold": args.gain_threshold,
            "passed": gain_passed,
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
