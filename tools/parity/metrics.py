"""Parity-specific comparison metrics.

These differ from retrieval-quality metrics (nDCG, Recall) because
parity has no ground-truth relevance set. Both adapters are treated
symmetrically; we measure how much they agree.
"""

from __future__ import annotations


def top_n_agreement(a: list[str], b: list[str], n: int = 1) -> float:
    """Return 1.0 if the top-n lists agree as sets, else 0.0.

    For n=1 this is equivalent to "did both adapters return the same
    top hit?".
    """
    if not a or not b:
        return 0.0
    return 1.0 if set(a[:n]) == set(b[:n]) else 0.0


def top_k_jaccard(a: list[str], b: list[str], k: int = 5) -> float:
    """Jaccard similarity of the top-k results.

    Symmetric and bounded in ``[0, 1]``. Returns 1.0 when the same
    documents appear (in any order) and 0.0 when there is no overlap.
    """
    if not a and not b:
        return 1.0
    set_a = set(a[:k])
    set_b = set(b[:k])
    union = set_a | set_b
    if not union:
        return 1.0
    return len(set_a & set_b) / len(union)


def kendall_tau(a: list[str], b: list[str], k: int = 10) -> float:
    """Approximate Kendall tau on the top-k positional alignment.

    Considers only documents that appear in both top-k lists and
    measures the fraction of concordant pairwise orderings. Returns
    ``1.0`` for identical orderings, ``-1.0`` for inverted, and ``0.0``
    when one list has no overlap with the other.
    """
    set_a = set(a[:k])
    set_b = set(b[:k])
    common = list(set_a & set_b)
    if len(common) < 2:
        return 1.0 if len(common) == 1 else 0.0

    rank_a = {doc_id: i for i, doc_id in enumerate(a[:k])}
    rank_b = {doc_id: i for i, doc_id in enumerate(b[:k])}

    pairs = 0
    concordant = 0
    for i in range(len(common)):
        for j in range(i + 1, len(common)):
            x, y = common[i], common[j]
            pairs += 1
            if (rank_a[x] - rank_a[y]) * (rank_b[x] - rank_b[y]) > 0:
                concordant += 1

    if pairs == 0:
        return 0.0
    return (2 * concordant / pairs) - 1.0


def parity_summary(
    per_query: list[dict[str, object]],
    divergence_threshold: float = 0.6,
) -> dict[str, object]:
    """Aggregate per-query parity results into a single summary.

    Plan exit criterion: at most 5 percent of queries can show greater
    than 5 percent retrieval divergence. We interpret divergence as
    "top-5 Jaccard below the threshold". The defaults make the gate
    explicit: at least 95 percent of queries must have top-5 Jaccard
    of at least 0.6.
    """
    if not per_query:
        return {
            "queries_total": 0,
            "comparable": False,
            "reason": "no queries evaluated",
        }

    n = len(per_query)
    top1 = sum(float(q.get("top_1_agreement", 0.0)) for q in per_query) / n
    jaccard = sum(float(q.get("top_5_jaccard", 0.0)) for q in per_query) / n
    tau = sum(float(q.get("kendall_tau", 0.0)) for q in per_query) / n
    divergent = sum(
        1
        for q in per_query
        if float(q.get("top_5_jaccard", 0.0)) < divergence_threshold
    )
    divergent_pct = divergent / n

    return {
        "queries_total": n,
        "comparable": True,
        "mean_top_1_agreement": round(top1, 4),
        "mean_top_5_jaccard": round(jaccard, 4),
        "mean_kendall_tau": round(tau, 4),
        "divergent_query_count": divergent,
        "divergent_query_fraction": round(divergent_pct, 4),
        "divergence_threshold": divergence_threshold,
        "gate_passed": divergent_pct <= 0.05,
    }
