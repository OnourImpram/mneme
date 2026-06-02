"""Evaluation runner, dataset adapters, and head-to-head comparator.

This module ships the runner infrastructure and format adapters only.
The datasets (LongMemEval, LoCoMo) and any competitor retrieve functions
(e.g. a claude-mem retrieve fn) are SUPPLIED BY THE OPERATOR at run time.
This module makes no superiority claim about any retrieval system.

Any public statement that one system "beats" or is "best" relative to
another requires an operator-run, published benchmark with full
experimental controls. The :func:`compare` function provides a purely
descriptive readout of the numbers produced in a single run.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass

from mneme_core.bench.metrics import (
    mean_reciprocal_rank,
    ndcg_at_k,
    recall_at_k,
)

RetrieveIds = Callable[[str], list[str | int]]
"""Query string -> ranked list of doc ids (best first)."""


@dataclass(frozen=True)
class EvalCase:
    """One evaluation case: a query and the doc ids that should be retrieved."""

    case_id: str
    query: str
    relevant_ids: tuple[str | int, ...]


@dataclass(frozen=True)
class CaseResult:
    """Per-case metric scores for a single (query, retrieve) evaluation."""

    case_id: str
    recall_at_k: float
    reciprocal_rank: float
    ndcg_at_k: float


@dataclass(frozen=True)
class EvalReport:
    """Aggregate evaluation report for one system over a case set."""

    system_name: str
    k: int
    n_cases: int
    mean_recall_at_k: float
    mean_mrr: float
    mean_ndcg_at_k: float
    per_case: tuple[CaseResult, ...]


def run_eval(
    cases: Iterable[EvalCase],
    retrieve: RetrieveIds,
    *,
    system_name: str,
    k: int = 10,
) -> EvalReport:
    """Run deterministic evaluation of *retrieve* over *cases*.

    For each case the retrieve function is called once. Metrics are
    computed by reusing :func:`~mneme_core.bench.metrics.recall_at_k`,
    :func:`~mneme_core.bench.metrics.mean_reciprocal_rank`, and
    :func:`~mneme_core.bench.metrics.ndcg_at_k`. An empty retrieve result
    scores zero on all metrics without raising. An empty case list returns
    an all-zero report without raising. The order of ``per_case`` matches
    the input iteration order.
    """
    case_results: list[CaseResult] = []

    for case in cases:
        retrieved: list[str | int] = retrieve(case.query)
        rel: tuple[str | int, ...] = case.relevant_ids

        r_at_k = recall_at_k(retrieved, rel, k)
        rr = mean_reciprocal_rank([(retrieved, rel)])
        n_at_k = ndcg_at_k(retrieved, rel, k)

        case_results.append(
            CaseResult(
                case_id=case.case_id,
                recall_at_k=r_at_k,
                reciprocal_rank=rr,
                ndcg_at_k=n_at_k,
            )
        )

    n = len(case_results)
    if n == 0:
        return EvalReport(
            system_name=system_name,
            k=k,
            n_cases=0,
            mean_recall_at_k=0.0,
            mean_mrr=0.0,
            mean_ndcg_at_k=0.0,
            per_case=(),
        )

    mean_r = sum(c.recall_at_k for c in case_results) / n
    mean_mrr = sum(c.reciprocal_rank for c in case_results) / n
    mean_n = sum(c.ndcg_at_k for c in case_results) / n

    return EvalReport(
        system_name=system_name,
        k=k,
        n_cases=n,
        mean_recall_at_k=mean_r,
        mean_mrr=mean_mrr,
        mean_ndcg_at_k=mean_n,
        per_case=tuple(case_results),
    )


def load_longmemeval(records: Iterable[dict[str, object]]) -> list[EvalCase]:
    """Map LongMemEval-style records to :class:`EvalCase`.

    Accepted key variants:

    * ``case_id``: ``"question_id"`` | ``"id"`` | ``"case_id"``
      (fallback ``"case-<i>"`` when none present).
    * ``query``: ``"question"`` | ``"query"`` | ``"input"``.
    * ``relevant_ids``: ``"answer_session_ids"`` | ``"relevant_ids"``
      | ``"evidence_ids"`` | ``"gold_ids"`` (empty list when absent).

    Records with no resolvable query are silently skipped. Never raises.
    """
    result: list[EvalCase] = []
    for i, rec in enumerate(records):
        query = _first_str(rec, ("question", "query", "input"))
        if query is None:
            continue
        case_id = _first_str(rec, ("question_id", "id", "case_id")) or f"case-{i}"
        rel_raw = _first_list(
            rec, ("answer_session_ids", "relevant_ids", "evidence_ids", "gold_ids")
        )
        relevant_ids: tuple[str | int, ...] = tuple(v for v in rel_raw if isinstance(v, (str, int)))
        result.append(EvalCase(case_id=case_id, query=query, relevant_ids=relevant_ids))
    return result


def load_locomo(records: Iterable[dict[str, object]]) -> list[EvalCase]:
    """Map LoCoMo-style records to :class:`EvalCase`.

    Accepted key variants:

    * ``case_id``: ``"sample_id"`` | ``"id"``.
    * ``query``: ``"question"`` | ``"query"``.
    * ``relevant_ids``: ``"evidence"`` | ``"relevant_ids"`` | ``"gold_ids"``
      (empty list when absent).

    Records with no resolvable query are silently skipped. Never raises.
    """
    result: list[EvalCase] = []
    for i, rec in enumerate(records):
        query = _first_str(rec, ("question", "query"))
        if query is None:
            continue
        case_id = _first_str(rec, ("sample_id", "id")) or f"case-{i}"
        rel_raw = _first_list(rec, ("evidence", "relevant_ids", "gold_ids"))
        relevant_ids: tuple[str | int, ...] = tuple(v for v in rel_raw if isinstance(v, (str, int)))
        result.append(EvalCase(case_id=case_id, query=query, relevant_ids=relevant_ids))
    return result


def head_to_head(
    cases: Iterable[EvalCase],
    systems: dict[str, RetrieveIds],
    *,
    k: int = 10,
) -> dict[str, EvalReport]:
    """Run :func:`run_eval` for each system over the same materialised case set.

    Cases are materialised once so every system is evaluated on an
    identical sequence. Returns ``{system_name: EvalReport}``. Deterministic.
    """
    materialised: list[EvalCase] = list(cases)
    return {
        name: run_eval(materialised, retrieve_fn, system_name=name, k=k)
        for name, retrieve_fn in systems.items()
    }


def compare(reports: dict[str, EvalReport]) -> dict[str, object]:
    """Tabulate mean metrics across systems and identify the leader per metric.

    Returns a dict with three keys:

    * ``"systems"``: sorted list of system names.
    * ``"metrics"``: ``{"recall_at_k": {name: val}, "mrr": {...},
      "ndcg_at_k": {...}}``.
    * ``"leader_by_metric"``: ``{"recall_at_k": <name>, ...}`` — the
      system with the highest value for each metric.

    IMPORTANT: ``leader_by_metric`` is a PURELY DESCRIPTIVE readout of
    this run's measured numbers. It is NOT a published superiority claim.
    Any public "best / beats X" assertion requires an operator-run,
    published benchmark with full experimental controls; this harness only
    measures. Ties are broken by choosing the lexicographically smallest
    name among the joint-maximum scorers, ensuring a deterministic result.
    """
    names = sorted(reports)
    recall_vals = {n: reports[n].mean_recall_at_k for n in names}
    mrr_vals = {n: reports[n].mean_mrr for n in names}
    ndcg_vals = {n: reports[n].mean_ndcg_at_k for n in names}

    def _leader(vals: dict[str, float]) -> str:
        if not vals:
            return ""
        max_val = max(vals.values())
        candidates = sorted(n for n, v in vals.items() if v == max_val)
        return candidates[0]

    return {
        "systems": names,
        "metrics": {
            "recall_at_k": recall_vals,
            "mrr": mrr_vals,
            "ndcg_at_k": ndcg_vals,
        },
        "leader_by_metric": {
            "recall_at_k": _leader(recall_vals),
            "mrr": _leader(mrr_vals),
            "ndcg_at_k": _leader(ndcg_vals),
        },
    }


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _first_str(rec: dict[str, object], keys: tuple[str, ...]) -> str | None:
    """Return the first non-empty string value found under any of *keys*."""
    for key in keys:
        val = rec.get(key)
        if isinstance(val, str) and val:
            return val
    return None


def _first_list(rec: dict[str, object], keys: tuple[str, ...]) -> list[object]:
    """Return the first list value found under any of *keys*, else ``[]``."""
    for key in keys:
        val = rec.get(key)
        if isinstance(val, list):
            return val
    return []
