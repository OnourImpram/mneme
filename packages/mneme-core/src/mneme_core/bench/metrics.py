"""Information-retrieval metric primitives.

Three retrieval metrics plus a latency-distribution helper. The retrieval
metrics are defined over a single (query, ranked_doc_ids,
relevant_doc_ids) triple. Aggregation across a query set is the caller's
responsibility - usually a plain arithmetic mean.

The contracts deliberately stay narrow:

* ``ndcg_at_k`` uses binary relevance with the standard
  ``DCG = sum(rel_i / log2(i + 1))`` formulation. ``IDCG`` is computed
  from the ideal binary ranking. Returns ``0.0`` when no relevant docs
  exist for the query.

* ``recall_at_k`` returns the fraction of relevant docs that appear in
  the top-k of the ranked list.

* ``mean_reciprocal_rank`` returns the mean of ``1/rank`` of the first
  relevant doc across a query set; queries with no relevant hit in the
  cutoff contribute zero.

* ``percentiles`` linearly interpolates the requested quantiles from a
  list of latency samples. Empty input returns NaN for each quantile.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Sequence


def ndcg_at_k(
    ranked_doc_ids: Sequence[object],
    relevant_doc_ids: Iterable[object],
    k: int,
) -> float:
    """Normalized Discounted Cumulative Gain at cutoff ``k``.

    Binary relevance: a doc is either relevant (gain 1) or not (gain 0).
    ``k`` must be >= 1. The top-``k`` slice of ``ranked_doc_ids`` is
    scored. ``relevant_doc_ids`` is consumed eagerly into a set so the
    caller may pass a generator.
    """
    if k < 1:
        raise ValueError("k must be >= 1")
    relevant = set(relevant_doc_ids)
    if not relevant:
        return 0.0
    dcg = 0.0
    for i, doc_id in enumerate(ranked_doc_ids[:k], start=1):
        if doc_id in relevant:
            dcg += 1.0 / math.log2(i + 1)
    ideal_hits = min(len(relevant), k)
    idcg = sum(1.0 / math.log2(i + 1) for i in range(1, ideal_hits + 1))
    return dcg / idcg if idcg > 0 else 0.0


def recall_at_k(
    ranked_doc_ids: Sequence[object],
    relevant_doc_ids: Iterable[object],
    k: int,
) -> float:
    """Recall at cutoff ``k``: relevant hits in top-k / total relevant."""
    if k < 1:
        raise ValueError("k must be >= 1")
    relevant = set(relevant_doc_ids)
    if not relevant:
        return 0.0
    top_k = set(ranked_doc_ids[:k])
    return len(relevant & top_k) / len(relevant)


def mean_reciprocal_rank(
    per_query: Iterable[tuple[Sequence[object], Iterable[object]]],
    cutoff: int | None = None,
) -> float:
    """Mean Reciprocal Rank across a query set.

    Each item is ``(ranked_doc_ids, relevant_doc_ids)``. Reciprocal rank
    is ``1 / rank`` of the first relevant doc (1-indexed). If no
    relevant doc appears within ``cutoff`` (or anywhere when ``cutoff``
    is ``None``), the query contributes zero. Returns ``0.0`` for an
    empty query set rather than NaN, matching common BEIR conventions.
    """
    total = 0.0
    n = 0
    for ranked, relevant_in in per_query:
        relevant = set(relevant_in)
        rr = 0.0
        iterator = ranked if cutoff is None else ranked[:cutoff]
        for i, doc_id in enumerate(iterator, start=1):
            if doc_id in relevant:
                rr = 1.0 / i
                break
        total += rr
        n += 1
    return total / n if n > 0 else 0.0


def percentiles(samples: Iterable[float], quantiles: Iterable[float]) -> dict[str, float]:
    """Linear-interpolation percentile sketch.

    Returns a dict keyed by ``pNN`` (e.g. ``p50``, ``p95``, ``p99``).
    Quantile inputs are floats in ``[0.0, 1.0]``. Empty input returns
    NaN for every requested quantile so downstream JSON serializers can
    still emit the structure.
    """
    sorted_samples = sorted(samples)
    out: dict[str, float] = {}
    if not sorted_samples:
        for q in quantiles:
            out[_quantile_key(q)] = float("nan")
        return out
    for q in quantiles:
        if q < 0.0 or q > 1.0:
            raise ValueError(f"quantile out of range: {q}")
        if q == 1.0:
            out[_quantile_key(q)] = float(sorted_samples[-1])
            continue
        if q == 0.0:
            out[_quantile_key(q)] = float(sorted_samples[0])
            continue
        pos = q * (len(sorted_samples) - 1)
        lower = int(math.floor(pos))
        upper = int(math.ceil(pos))
        if lower == upper:
            out[_quantile_key(q)] = float(sorted_samples[lower])
        else:
            frac = pos - lower
            out[_quantile_key(q)] = float(
                sorted_samples[lower] * (1.0 - frac)
                + sorted_samples[upper] * frac
            )
    return out


def _quantile_key(q: float) -> str:
    # p50/p95/p99 style keys for the common cases; otherwise pNN.NN.
    pct = q * 100.0
    if abs(pct - round(pct)) < 1e-9:
        return f"p{int(round(pct))}"
    return f"p{pct:.2f}".rstrip("0").rstrip(".")
