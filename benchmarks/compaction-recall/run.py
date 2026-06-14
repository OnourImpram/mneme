"""Benchmark F - Compaction Recall.

Measures how much information the CCE self-heal recovers after a host
compaction drops a deterministic subset of session working-set items.

The benchmark is a MEASUREMENT, not a claim.  It builds a seeded synthetic
fixture, simulates compaction, runs ``detect_dropped`` plus greedy rehydration
within ``rehydration_token_budget`` tokens, and reports the difference in
recall between the baseline (no self-heal) and the mneme self-heal condition.

A negative probe verifies that the self-heal never *invents* a fact that was
not in the original checkpoint (recovered ⊆ original).  All numbers are
computed from the fixture; none are hardcoded.

ADR-012 caveat: this is a synthetic seeded regression anchor.  The compaction
pattern (which items survive) and the rehydration budget are under our
control.  The numbers bound regression, not real-world accuracy.

Usage:

    python benchmarks/compaction-recall/run.py \\
        --seed 42 --output result.json --output-format table
    python benchmarks/compaction-recall/run.py \\
        --seed 42 --output /tmp/cr.json --output-format json
"""

from __future__ import annotations

import argparse
import json
import random
import sys
import tempfile
from pathlib import Path

# Allow running from the repo root without installing the package.
_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_REPO_ROOT / "packages" / "mneme-core" / "src"))

from mneme_core.cce.budget import estimate_tokens  # noqa: E402
from mneme_core.cce.checkpoint import (  # noqa: E402
    Checkpoint,
    WorkingSetItem,
    make_anchor,
)
from mneme_core.cce.config import (  # noqa: E402
    DEFAULT_REHYDRATION_TOKEN_BUDGET,
    DEFAULT_SALIENCE_WEIGHTS,
)
from mneme_core.cce.loss_detect import detect_dropped  # noqa: E402
from mneme_core.cce.salience import score_item  # noqa: E402

# ---------------------------------------------------------------------------
# Benchmark parameters
# ---------------------------------------------------------------------------

# Total number of synthetic facts in the working set.
_K_FACTS = 50

# Fraction of facts the simulated compaction retains (survives).
# ~60 % are dropped, ~40 % survive — challenging but recoverable.
_SURVIVAL_RATE = 0.40

# Kinds to cycle through when generating synthetic facts.
_KINDS = ["decision", "todo", "recent_edit", "intent"]


# ---------------------------------------------------------------------------
# Fixture builders
# ---------------------------------------------------------------------------


def _build_facts(seed: int, k: int) -> list[WorkingSetItem]:
    """Build *k* deterministic synthetic WorkingSetItems from *seed*.

    Each item gets a stable salience drawn from DEFAULT_SALIENCE_WEIGHTS
    so the scoring is consistent with the production config.
    """
    rng = random.Random(seed)
    weights = DEFAULT_SALIENCE_WEIGHTS
    items: list[WorkingSetItem] = []
    for i in range(k):
        kind = _KINDS[i % len(_KINDS)]
        # Unique, non-overlapping text so substring matching is unambiguous.
        text = f"fact-{i:04d}-{_kind_label(kind)}-seed{seed}-token{rng.randint(10000, 99999)}"
        sal = score_item(kind, {kind: 1.0}, weights)
        items.append(WorkingSetItem(kind=kind, text=text, salience=sal))
    return items


def _kind_label(kind: str) -> str:
    return {"decision": "dec", "todo": "todo", "recent_edit": "edit", "intent": "int"}.get(
        kind, kind
    )


def _build_checkpoint(items: list[WorkingSetItem], seed: int) -> Checkpoint:
    """Wrap items in a Checkpoint with a deterministic anchor."""
    created = "2026-06-14T00:00:00+00:00"
    session_id = f"bench-compaction-seed{seed}"
    anchor = make_anchor(created, session_id)
    return Checkpoint(
        anchor=anchor,
        created=created,
        session_id=session_id,
        prev_anchor=None,
        items=tuple(items),
    )


def _choose_survivors(items: list[WorkingSetItem], seed: int) -> frozenset[int]:
    """Choose indices that survive compaction — deterministic via seeded RNG.

    Survivors are the first ``floor(len * SURVIVAL_RATE)`` items when the list
    is shuffled by a seeded RNG.  This is independent of item order so the
    result is stable regardless of sort order.
    """
    rng = random.Random(seed + 1)  # offset so survivor selection differs from item generation
    indices = list(range(len(items)))
    rng.shuffle(indices)
    n_survivors = max(1, round(len(items) * _SURVIVAL_RATE))
    return frozenset(indices[:n_survivors])


def _write_post_compaction_transcript(
    items: list[WorkingSetItem],
    survivor_indices: frozenset[int],
    transcript_path: Path,
) -> None:
    """Write a JSONL transcript containing only the surviving facts.

    Each surviving item's text is embedded in a plain assistant message so
    ``detect_dropped`` can find it via substring search.
    """
    with transcript_path.open("w", encoding="utf-8") as fp:
        for idx, item in enumerate(items):
            if idx in survivor_indices:
                record = {
                    "type": "assistant",
                    "message": {
                        "role": "assistant",
                        "content": [{"type": "text", "text": item.text}],
                    },
                }
                fp.write(json.dumps(record, ensure_ascii=False) + "\n")


# ---------------------------------------------------------------------------
# Recall measurement
# ---------------------------------------------------------------------------


def _compute_baseline_recall(
    items: list[WorkingSetItem],
    survivor_indices: frozenset[int],
) -> float:
    """Recall without self-heal: facts present in post-compaction transcript / K."""
    k = len(items)
    if k == 0:
        return 0.0
    return len(survivor_indices) / k


def _compute_selfheal_recall(
    items: list[WorkingSetItem],
    survivor_indices: frozenset[int],
    checkpoint: Checkpoint,
    transcript_path: Path,
    token_budget: int,
) -> tuple[float, frozenset[str]]:
    """Recall with self-heal: (survivors ∪ rehydrated) / K.

    Rehydration is greedy: items are added highest-salience first until the
    token budget is exhausted.  Returns (recall_at_k, set_of_recovered_texts).
    """
    k = len(items)
    if k == 0:
        return 0.0, frozenset()

    # Which items actually survived in the transcript?
    surviving_texts: set[str] = {items[i].text for i in survivor_indices}

    # Detect what was dropped by the compaction.
    dropped_items = detect_dropped(checkpoint, transcript_path)
    # detect_dropped already sorts by salience descending.

    # Greedy rehydration within token budget.
    tokens_used = 0
    recovered_texts: set[str] = set()
    for item in dropped_items:
        cost = estimate_tokens(item.text)
        if tokens_used + cost > token_budget:
            break
        recovered_texts.add(item.text)
        tokens_used += cost

    recovered_set: set[str] = surviving_texts | recovered_texts
    recall = len(recovered_set & {item.text for item in items}) / k
    return recall, frozenset(recovered_texts)


def _negative_probe(
    recovered_texts: frozenset[str],
    original_texts: frozenset[str],
) -> dict[str, object]:
    """Confirm self-heal never invents a fact not in the original checkpoint.

    Pass criterion: ``recovered_texts ⊆ original_texts``.
    Any invented fact (recovered but not original) is a hallucination defect.
    """
    invented = recovered_texts - original_texts
    passed = len(invented) == 0
    return {
        "criterion": "recovered facts must be a subset of original checkpoint facts",
        "recovered_count": len(recovered_texts),
        "original_count": len(original_texts),
        "invented_count": len(invented),
        "all_passed": passed,
    }


# ---------------------------------------------------------------------------
# JSON output
# ---------------------------------------------------------------------------


def write_json(payload: object, output_path: Path) -> None:
    """Write benchmark JSON as UTF-8 without BOM on every platform."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Benchmark F - Compaction Recall"
    )
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Optional path to write JSON output as UTF-8 without BOM.",
    )
    parser.add_argument(
        "--output-format",
        choices=("json", "table"),
        default="json",
    )
    parser.add_argument(
        "--facts",
        type=int,
        default=_K_FACTS,
        help="Number of synthetic facts in the working set.",
    )
    parser.add_argument(
        "--token-budget",
        type=int,
        default=DEFAULT_REHYDRATION_TOKEN_BUDGET,
        help="Token budget for rehydration (mirrors config.rehydration_token_budget).",
    )
    parser.add_argument(
        "--hardware-output",
        type=Path,
        default=None,
        help="Optional path to write hardware.json alongside the result (no-op, reserved).",
    )
    args = parser.parse_args()

    seed: int = args.seed
    k: int = args.facts
    token_budget: int = args.token_budget

    # Build deterministic fixture.
    items = _build_facts(seed, k)
    checkpoint = _build_checkpoint(items, seed)
    survivor_indices = _choose_survivors(items, seed)
    n_dropped = k - len(survivor_indices)

    with tempfile.TemporaryDirectory(prefix="mneme-bench-compaction-") as tmp:
        transcript_path = Path(tmp) / "post_compaction.jsonl"
        _write_post_compaction_transcript(items, survivor_indices, transcript_path)

        baseline_recall = _compute_baseline_recall(items, survivor_indices)
        selfheal_recall, recovered_texts = _compute_selfheal_recall(
            items,
            survivor_indices,
            checkpoint,
            transcript_path,
            token_budget,
        )

    original_texts = frozenset(item.text for item in items)
    neg_probe = _negative_probe(recovered_texts, original_texts)

    headline_gain = round(selfheal_recall - baseline_recall, 6)

    payload: dict[str, object] = {
        "benchmark": "compaction-recall",
        "seed": seed,
        "corpus": {
            "facts": k,
            "survivors": len(survivor_indices),
            "dropped": n_dropped,
            "survival_rate": round(len(survivor_indices) / k, 4),
            "token_budget": token_budget,
        },
        "conditions": {
            "baseline_no_selfheal": {
                "recall_at_k": round(baseline_recall, 6),
            },
            "mneme_selfheal": {
                "recall_at_k": round(selfheal_recall, 6),
            },
        },
        "headline_recall_gain": headline_gain,
        "negative_probe": neg_probe,
    }

    if args.output is not None:
        write_json(payload, args.output)

    if args.output_format == "json" and args.output is None:
        json.dump(payload, sys.stdout, indent=2)
        sys.stdout.write("\n")
    elif args.output_format == "table":
        sys.stdout.write("Benchmark F - Compaction Recall\n")
        sys.stdout.write(
            f"  corpus: {k} facts, {len(survivor_indices)} survivors, "
            f"{n_dropped} dropped (seed={seed})\n"
        )
        sys.stdout.write(
            f"  baseline_no_selfheal  recall_at_k: {baseline_recall:.4f}\n"
        )
        sys.stdout.write(
            f"  mneme_selfheal        recall_at_k: {selfheal_recall:.4f}\n"
        )
        sys.stdout.write(
            f"  headline_recall_gain: {headline_gain:+.4f}\n"
        )
        probe = payload["negative_probe"]
        assert isinstance(probe, dict)
        sys.stdout.write(
            f"  negative_probe: all_passed={probe['all_passed']} "
            f"(invented={probe['invented_count']})\n"
        )

    return 0


if __name__ == "__main__":
    sys.exit(main())
