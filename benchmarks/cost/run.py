"""Benchmark C - Cost (Adaptive Context Layer effectiveness).

Quantifies the four primary token-saving primitives of the Adaptive
Context Layer:

* ``distill.shell_compress`` byte-ratio on a representative bash
  output corpus.
* ``distill.injection_dedup`` skip rate over a simulated 20-turn
  session that re-encounters the same docs.
* ``distill.adaptive_topk`` decision sweep across the operational
  range of context-window usage.
* ``distill.compressed_format`` token-equivalent payload size for the
  full / keypoints / ref injection levels.

The token count is approximated as ``ceil(len(text) / 4)``. That is
the industry-standard heuristic for GPT-family BPE tokenizers and is
accurate to within roughly 10% on prose. The benchmark surfaces the
heuristic explicitly so readers can recompute with a real tokenizer
when needed.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_REPO_ROOT / "packages" / "mneme-core" / "src"))

from mneme_core.bench.hardware import (  # noqa: E402
    capture_hardware,
    write_hardware_json,
)
from mneme_core.distill.adaptive_topk import TopKPolicy, adaptive_topk  # noqa: E402
from mneme_core.distill.compressed_format import (  # noqa: E402
    InjectionFormat,
    InjectionInput,
    render_injection,
)
from mneme_core.distill.injection_dedup import (  # noqa: E402
    InjectionTracker,
    has_injected,
    mark_injected,
)
from mneme_core.distill.shell_compress import compress_shell_output  # noqa: E402

CHARS_PER_TOKEN = 4


def write_json(payload: object, output_path: Path) -> None:
    """Write benchmark JSON as UTF-8 without BOM on every platform."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def approx_tokens(text: str) -> int:
    """Heuristic char-count -> token-count conversion."""
    return math.ceil(len(text) / CHARS_PER_TOKEN) if text else 0


# Representative bash outputs the typical Claude Code session produces.
def _build_shell_corpus() -> list[tuple[str, str]]:
    listing_lines = "\n".join(
        f"-rw-r--r--  1 user group  {1000 + i * 17}  May 19 10:0{i % 10}  file_{i:03d}.txt"
        for i in range(40)
    )
    stack_lines = "\n".join(
        f"  at function_{i} (/repo/src/module/{i % 5}.py:{50 + i})"
        for i in range(15)
    )
    redundant_log = "\n".join(["[INFO] tick"] * 12)
    pip_output = (
        "Collecting package-a==1.0\n"
        "  Downloading package_a-1.0-py3-none-any.whl (12 kB)\n"
        "Collecting package-b==2.0\n"
        "  Downloading package_b-2.0-py3-none-any.whl (8 kB)\n"
        "Collecting package-c==3.0\n"
        "  Downloading package_c-3.0-py3-none-any.whl (4 kB)\n"
        "Installing collected packages: package-c, package-b, package-a\n"
        "Successfully installed package-a-1.0 package-b-2.0 package-c-3.0\n"
    )
    ansi_log = (
        "\x1b[32mPASSED\x1b[0m test_one\n"
        "\x1b[32mPASSED\x1b[0m test_two\n"
        "\x1b[31mFAILED\x1b[0m test_three\n"
    )
    return [
        ("ls_listing_40", f"Directory listing:\n{listing_lines}\n"),
        ("python_stack_15", f"Traceback:\n{stack_lines}\n"),
        ("redundant_log_12", redundant_log),
        ("pip_install_output", pip_output),
        ("ansi_color_log", ansi_log),
    ]


def measure_shell_compress() -> dict[str, float | list[dict[str, float | str | int]]]:
    """Run ``compress_shell_output`` on each corpus entry, report ratios."""
    corpus = _build_shell_corpus()
    per_entry: list[dict[str, float | str | int]] = []
    total_in = 0
    total_out = 0
    for label, text in corpus:
        stats = compress_shell_output(text)
        total_in += stats.original_bytes
        total_out += stats.compressed_bytes
        per_entry.append(
            {
                "label": label,
                "original_bytes": stats.original_bytes,
                "compressed_bytes": stats.compressed_bytes,
                "ratio": stats.ratio,
                "approx_tokens_saved": approx_tokens(text)
                - approx_tokens(stats.compressed_text),
            }
        )
    overall_ratio = total_out / total_in if total_in > 0 else 1.0
    return {
        "samples": len(corpus),
        "total_original_bytes": total_in,
        "total_compressed_bytes": total_out,
        "overall_ratio": round(overall_ratio, 4),
        "overall_reduction_percent": round(100.0 * (1.0 - overall_ratio), 2),
        "per_entry": per_entry,
    }


def measure_injection_dedup(
    turns: int = 20,
    unique_docs: int = 5,
) -> dict[str, int | float]:
    """Simulate a session where every turn injects from a small doc pool.

    Realistic shape: 20 turns, 5 unique docs cycled. First time a doc
    is encountered we ``mark_injected``; subsequent encounters report a
    skip via ``has_injected``. The skip count divided by total
    encounters is the token-saving signal.
    """
    tracker = InjectionTracker(session_id="bench-cost-c")
    encounters = 0
    skipped = 0
    for turn in range(turns):
        for d in range(unique_docs):
            content_hash = f"doc-{d:02d}"
            encounters += 1
            if has_injected(tracker, content_hash):
                skipped += 1
            else:
                mark_injected(tracker, content_hash)
    skip_rate = skipped / encounters if encounters else 0.0
    return {
        "turns": turns,
        "unique_docs": unique_docs,
        "encounters": encounters,
        "skipped": skipped,
        "skip_rate": round(skip_rate, 4),
        "skip_percent": round(100.0 * skip_rate, 2),
    }


def measure_adaptive_topk() -> list[dict[str, int]]:
    """Walk through representative context-usage points."""
    policy = TopKPolicy()
    points = [0, 1_000, 5_000, 10_000, 25_000, 50_000, 100_000]
    return [
        {"context_tokens_used": p, "top_k": adaptive_topk(p, policy)}
        for p in points
    ]


def measure_compressed_format() -> dict[str, dict[str, int | float]]:
    """Compare full / keypoints / ref payload sizes for one doc."""
    doc = InjectionInput(
        path="patterns/use-rrf-for-mixed-text.md",
        title="Use RRF for mixed-language vault searches",
        body=(
            "When the operator's vault contains both English and Turkish "
            "text, single-leg retrieval underperforms because BM25 cannot "
            "see lexical equivalents like 'compaction' and 'consolidation'. "
            "Fusing FTS5 with a benchmark surrogate via RRF k=60 lifts nDCG@5 by "
            "approximately nine points on the synthetic corpus and trends "
            "the same direction on real-world Phase J dogfood data."
        ),
        key_points=[
            "Use RRF when corpus has multilingual content.",
            "Set k=60 per Cormack et al. 2009.",
            "Expect ~9 point nDCG@5 lift over the single-leg synthetic baseline.",
        ],
    )
    out: dict[str, dict[str, int | float]] = {}
    full_text = render_injection(doc, InjectionFormat.FULL)
    full_tokens = approx_tokens(full_text)
    for fmt in (InjectionFormat.FULL, InjectionFormat.KEYPOINTS, InjectionFormat.REF):
        text = render_injection(doc, fmt)
        tokens = approx_tokens(text)
        out[fmt.value] = {
            "bytes": len(text),
            "approx_tokens": tokens,
            "saving_vs_full_percent": (
                round(100.0 * (1.0 - tokens / full_tokens), 2)
                if full_tokens
                else 0.0
            ),
        }
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description="Benchmark C - cost")
    parser.add_argument(
        "--output-format",
        choices=("json", "table"),
        default="json",
    )
    parser.add_argument("--turns", type=int, default=20)
    parser.add_argument("--unique-docs", type=int, default=5)
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

    payload = {
        "benchmark": "cost",
        "seed": args.seed,
        "token_heuristic": f"ceil(len(text)/{CHARS_PER_TOKEN})",
        "shell_compress": measure_shell_compress(),
        "injection_dedup": measure_injection_dedup(
            turns=args.turns, unique_docs=args.unique_docs
        ),
        "adaptive_topk": measure_adaptive_topk(),
        "compressed_format": measure_compressed_format(),
    }

    if args.hardware_output is not None:
        write_hardware_json(capture_hardware(seed=args.seed), args.hardware_output)

    if args.output is not None:
        write_json(payload, args.output)

    if args.output_format == "json" and args.output is None:
        json.dump(payload, sys.stdout, indent=2)
        sys.stdout.write("\n")
    elif args.output_format == "table":
        sc = payload["shell_compress"]
        sys.stdout.write("Benchmark C - cost\n")
        sys.stdout.write(
            f"  shell_compress reduction: {sc['overall_reduction_percent']}%\n"
        )
        sys.stdout.write(
            f"  injection_dedup skip rate: "
            f"{payload['injection_dedup']['skip_percent']}%\n"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
