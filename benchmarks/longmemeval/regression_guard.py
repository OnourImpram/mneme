"""CI regression guard for the LongMemEval benchmark.

Reads a fresh result JSON (same shape as baseline.json) and asserts each
metric meets the locked baseline within a tolerance of 0.05.  Exits 1 on
any failure so CI catches retrieval regressions automatically.

Usage:

    python benchmarks/longmemeval/regression_guard.py result.json
    python benchmarks/longmemeval/regression_guard.py result.json \\
        --baseline benchmarks/longmemeval/baseline.json \\
        --tolerance 0.05
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_BASELINE_PATH = Path(__file__).parent / "baseline.json"

_GUARDED_METRICS: tuple[str, ...] = (
    "recall_at_1",
    "recall_at_5",
    "recall_at_10",
    "mrr_at_10",
    "abstention_accuracy",
)


def _load_json(path: Path) -> dict:
    """Load JSON from path, tolerating UTF-8 BOM (e.g. PowerShell redirect)."""
    raw = path.read_bytes()
    for encoding in ("utf-8-sig", "utf-8", "utf-16"):
        try:
            return json.loads(raw.decode(encoding))  # type: ignore[return-value]
        except (UnicodeDecodeError, json.JSONDecodeError):
            continue
    raise ValueError(f"Cannot parse JSON from {path}")


def check_regression(
    result_metrics: dict[str, float],
    baseline_metrics: dict[str, float],
    tolerance: float = 0.05,
) -> tuple[bool, str]:
    """Compare result_metrics against baseline_metrics.

    A metric passes when: result >= baseline - tolerance.

    Returns:
        (overall_passed, report_string)
    """
    lines: list[str] = []
    overall = True

    for metric in _GUARDED_METRICS:
        if metric not in baseline_metrics:
            continue
        baseline_val = float(baseline_metrics[metric])
        result_val = float(result_metrics.get(metric, 0.0))
        drop = round(baseline_val - result_val, 10)
        passed = drop <= tolerance
        if not passed:
            overall = False
        lines.append(
            f"  {metric}: baseline={baseline_val:.4f}  result={result_val:.4f}"
            f"  drop={drop:.4f}  tolerance={tolerance:.4f}  {'PASS' if passed else 'FAIL'}"
        )

    report = "\n".join(lines)
    return overall, report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Regression guard for LongMemEval benchmark."
    )
    parser.add_argument("result", type=Path, help="Fresh result JSON to check.")
    parser.add_argument(
        "--baseline",
        type=Path,
        default=_BASELINE_PATH,
        help="Locked baseline JSON to compare against.",
    )
    parser.add_argument(
        "--tolerance",
        type=float,
        default=0.05,
        help="Allowed absolute drop per metric (default 0.05).",
    )
    args = parser.parse_args(argv)

    baseline_data = _load_json(args.baseline)
    result_data = _load_json(args.result)

    baseline_metrics: dict[str, float] = baseline_data.get("results", {})
    result_metrics: dict[str, float] = result_data.get("results", {})

    passed, report = check_regression(result_metrics, baseline_metrics, args.tolerance)

    status = "PASSED" if passed else "FAILED"
    output = {
        "overall_passed": passed,
        "tolerance": args.tolerance,
        "checks": report,
    }
    json.dump(output, sys.stdout, indent=2)
    sys.stdout.write("\n")

    if not passed:
        sys.stderr.write(f"regression_guard: {status}\n{report}\n")

    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
