"""CI latency guard for Benchmark B.

Loads a fresh ``result.json`` and fails the build if the Stop-hook
proxy p95 exceeds ``--threshold-ms``. The Zero-LLM-Stop claim depends
on this number staying low, so the guard runs after every
``bench.yml`` invocation.

Default threshold of 1000ms matches the constraint documented in
``docs/CONSTRAINTS.md``. CI can override per environment.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description="Latency p95 guard for Bench B.")
    parser.add_argument("result", type=Path, help="Fresh result.json to check.")
    parser.add_argument(
        "--threshold-ms",
        type=float,
        default=1000.0,
        help="Maximum allowed Stop-hook proxy p95 in milliseconds.",
    )
    parser.add_argument(
        "--metric-path",
        type=str,
        default="stop_hook_proxy_ms.p95",
        help="Dotted path inside result.json. Defaults to the Stop hook p95.",
    )
    args = parser.parse_args()

    result = json.loads(args.result.read_text(encoding="utf-8"))
    cur: object = result
    for part in args.metric_path.split("."):
        if not isinstance(cur, dict):
            raise KeyError(f"Path segment {part!r} hit a non-dict")
        cur = cur[part]
    if not isinstance(cur, int | float):
        raise TypeError(f"Resolved {args.metric_path} to a non-numeric: {cur!r}")
    p95 = float(cur)
    if math.isnan(p95):
        json.dump(
            {
                "metric": args.metric_path,
                "value": "NaN",
                "threshold_ms": args.threshold_ms,
                "passed": False,
                "reason": "metric is NaN; benchmark produced no samples",
            },
            sys.stdout,
            indent=2,
        )
        sys.stdout.write("\n")
        return 1

    passed = p95 <= args.threshold_ms
    json.dump(
        {
            "metric": args.metric_path,
            "value_ms": p95,
            "threshold_ms": args.threshold_ms,
            "passed": passed,
        },
        sys.stdout,
        indent=2,
    )
    sys.stdout.write("\n")
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
