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

import re
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass

from mneme_core.bench.metrics import (
    mean_reciprocal_rank,
    ndcg_at_k,
    precision_at_k,
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
    precision_at_k: float = 0.0


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
    mean_precision_at_k: float = 0.0


class DatasetSchemaError(ValueError):
    """Raised when an official benchmark record violates its public schema."""


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
        p_at_k = precision_at_k(retrieved, rel, k)
        rr = mean_reciprocal_rank([(retrieved, rel)])
        n_at_k = ndcg_at_k(retrieved, rel, k)

        case_results.append(
            CaseResult(
                case_id=case.case_id,
                recall_at_k=r_at_k,
                reciprocal_rank=rr,
                ndcg_at_k=n_at_k,
                precision_at_k=p_at_k,
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
            mean_precision_at_k=0.0,
        )

    mean_r = sum(c.recall_at_k for c in case_results) / n
    mean_mrr = sum(c.reciprocal_rank for c in case_results) / n
    mean_n = sum(c.ndcg_at_k for c in case_results) / n
    mean_p = sum(c.precision_at_k for c in case_results) / n

    return EvalReport(
        system_name=system_name,
        k=k,
        n_cases=n,
        mean_recall_at_k=mean_r,
        mean_mrr=mean_mrr,
        mean_ndcg_at_k=mean_n,
        per_case=tuple(case_results),
        mean_precision_at_k=mean_p,
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


_LONGMEMEVAL_QUESTION_TYPES = frozenset(
    {
        "single-session-user",
        "single-session-assistant",
        "single-session-preference",
        "temporal-reasoning",
        "knowledge-update",
        "multi-session",
    }
)
_LOCOMO_SESSION_RE = re.compile(r"^session_(\d+)$")


def load_longmemeval_official(
    records: Iterable[object], *, include_abstention: bool = False
) -> list[EvalCase]:
    """Validate and adapt records from the official LongMemEval JSON schema.

    This strict adapter follows the public LongMemEval dataset contract. It
    accepts unknown extension fields but rejects missing, mistyped, duplicate,
    or internally inconsistent required fields. Retrieval evaluation excludes
    ``question_id`` values ending in ``_abs`` by default because they have no
    answer session. Set ``include_abstention`` to retain them. It never
    downloads data.
    """
    result: list[EvalCase] = []
    seen_case_ids: set[str] = set()
    for record_index, raw_record in enumerate(records):
        path = f"records[{record_index}]"
        record = _as_mapping(raw_record, path)
        case_id = _required_str(record, "question_id", path)
        if case_id in seen_case_ids:
            raise DatasetSchemaError(f"{path}.question_id is duplicated: {case_id!r}")
        seen_case_ids.add(case_id)

        question_type = _required_str(record, "question_type", path)
        if question_type not in _LONGMEMEVAL_QUESTION_TYPES:
            raise DatasetSchemaError(f"{path}.question_type is not an official LongMemEval value")
        query = _required_str(record, "question", path)
        _required_str(record, "answer", path, allow_empty=True)
        _required_str(record, "question_date", path)

        session_ids = _string_list(
            _required_list(record, "haystack_session_ids", path),
            f"{path}.haystack_session_ids",
        )
        if len(set(session_ids)) != len(session_ids):
            raise DatasetSchemaError(f"{path}.haystack_session_ids contains duplicates")
        dates = _string_list(
            _required_list(record, "haystack_dates", path),
            f"{path}.haystack_dates",
        )
        sessions = _required_list(record, "haystack_sessions", path)
        if len(session_ids) != len(dates) or len(session_ids) != len(sessions):
            raise DatasetSchemaError(
                f"{path} haystack_session_ids, haystack_dates, and "
                "haystack_sessions must have equal lengths"
            )
        for session_index, raw_session in enumerate(sessions):
            session_path = f"{path}.haystack_sessions[{session_index}]"
            session = _as_list(raw_session, session_path)
            for turn_index, raw_turn in enumerate(session):
                turn_path = f"{session_path}[{turn_index}]"
                turn = _as_mapping(raw_turn, turn_path)
                role = _required_str(turn, "role", turn_path)
                if role not in {"user", "assistant"}:
                    raise DatasetSchemaError(f"{turn_path}.role must be 'user' or 'assistant'")
                _required_str(turn, "content", turn_path, allow_empty=True)
                has_answer = turn.get("has_answer")
                if has_answer is not None and not isinstance(has_answer, bool):
                    raise DatasetSchemaError(f"{turn_path}.has_answer must be boolean")

        answer_session_ids = _string_list(
            _required_list(record, "answer_session_ids", path),
            f"{path}.answer_session_ids",
        )
        if len(set(answer_session_ids)) != len(answer_session_ids):
            raise DatasetSchemaError(f"{path}.answer_session_ids contains duplicates")
        unknown_ids = set(answer_session_ids) - set(session_ids)
        if unknown_ids:
            raise DatasetSchemaError(
                f"{path}.answer_session_ids references unknown sessions: {sorted(unknown_ids)!r}"
            )
        if include_abstention or not case_id.endswith("_abs"):
            result.append(
                EvalCase(
                    case_id=case_id,
                    query=query,
                    relevant_ids=tuple(answer_session_ids),
                )
            )
    return result


def load_locomo_official(records: Iterable[object]) -> list[EvalCase]:
    """Validate and flatten official LoCoMo samples into retrieval cases.

    The official file is conversation-level. Every top-level sample contains a
    ``conversation`` object and a nested ``qa`` list. This adapter validates
    that shape and emits one :class:`EvalCase` per QA annotation.
    """
    result: list[EvalCase] = []
    seen_sample_ids: set[str] = set()
    for sample_index, raw_sample in enumerate(records):
        path = f"records[{sample_index}]"
        sample = _as_mapping(raw_sample, path)
        sample_id = _required_str(sample, "sample_id", path)
        if sample_id in seen_sample_ids:
            raise DatasetSchemaError(f"{path}.sample_id is duplicated: {sample_id!r}")
        seen_sample_ids.add(sample_id)

        conversation = _as_mapping(sample.get("conversation"), f"{path}.conversation")
        speaker_a = _required_str(conversation, "speaker_a", f"{path}.conversation")
        speaker_b = _required_str(conversation, "speaker_b", f"{path}.conversation")
        speakers = {speaker_a, speaker_b}
        session_keys = sorted(
            (
                (int(match.group(1)), key)
                for key in conversation
                if (match := _LOCOMO_SESSION_RE.fullmatch(key)) is not None
            ),
            key=lambda item: item[0],
        )
        if not session_keys:
            raise DatasetSchemaError(f"{path}.conversation has no session_<num> lists")

        dialog_ids: set[str] = set()
        for session_number, session_key in session_keys:
            session_path = f"{path}.conversation.{session_key}"
            session = _as_list(conversation[session_key], session_path)
            date_key = f"session_{session_number}_date_time"
            _required_str(conversation, date_key, f"{path}.conversation")
            for turn_index, raw_turn in enumerate(session):
                turn_path = f"{session_path}[{turn_index}]"
                turn = _as_mapping(raw_turn, turn_path)
                speaker = _required_str(turn, "speaker", turn_path)
                if speaker not in speakers:
                    raise DatasetSchemaError(
                        f"{turn_path}.speaker does not match speaker_a or speaker_b"
                    )
                dialog_id = _required_str(turn, "dia_id", turn_path)
                if dialog_id in dialog_ids:
                    raise DatasetSchemaError(f"{turn_path}.dia_id is duplicated")
                dialog_ids.add(dialog_id)
                _required_str(turn, "text", turn_path, allow_empty=True)

        qa_records = _required_list(sample, "qa", path)
        for qa_index, raw_qa in enumerate(qa_records):
            qa_path = f"{path}.qa[{qa_index}]"
            qa = _as_mapping(raw_qa, qa_path)
            query = _required_str(qa, "question", qa_path)
            _required_str(qa, "answer", qa_path, allow_empty=True)
            category = qa.get("category")
            valid_category = (
                isinstance(category, int)
                and not isinstance(category, bool)
                or isinstance(category, str)
                and bool(category)
            )
            if not valid_category:
                raise DatasetSchemaError(
                    f"{qa_path}.category must be a non-empty string or integer"
                )
            evidence = _optional_string_list(qa, "evidence", qa_path)
            unknown_evidence = set(evidence) - dialog_ids
            if unknown_evidence:
                raise DatasetSchemaError(
                    f"{qa_path}.evidence references unknown dialog ids: "
                    f"{sorted(unknown_evidence)!r}"
                )
            result.append(
                EvalCase(
                    case_id=f"{sample_id}:qa-{qa_index}",
                    query=query,
                    relevant_ids=tuple(evidence),
                )
            )
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
    * ``"metrics"``: ``{"recall_at_k": {name: val},
      "precision_at_k": {...}, "mrr": {...}, "ndcg_at_k": {...}}``.
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
    precision_vals = {n: reports[n].mean_precision_at_k for n in names}
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
            "precision_at_k": precision_vals,
            "mrr": mrr_vals,
            "ndcg_at_k": ndcg_vals,
        },
        "leader_by_metric": {
            "recall_at_k": _leader(recall_vals),
            "precision_at_k": _leader(precision_vals),
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


def _as_mapping(value: object, path: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise DatasetSchemaError(f"{path} must be a JSON object")
    if any(not isinstance(key, str) for key in value):
        raise DatasetSchemaError(f"{path} must use string keys")
    return value


def _as_list(value: object, path: str) -> list[object]:
    if not isinstance(value, list):
        raise DatasetSchemaError(f"{path} must be a JSON array")
    return value


def _required_str(
    record: Mapping[str, object],
    key: str,
    path: str,
    *,
    allow_empty: bool = False,
) -> str:
    value = record.get(key)
    if not isinstance(value, str) or (not allow_empty and not value):
        qualifier = "a string" if allow_empty else "a non-empty string"
        raise DatasetSchemaError(f"{path}.{key} must be {qualifier}")
    return value


def _required_list(record: Mapping[str, object], key: str, path: str) -> list[object]:
    return _as_list(record.get(key), f"{path}.{key}")


def _string_list(values: list[object], path: str) -> list[str]:
    result: list[str] = []
    for index, value in enumerate(values):
        if not isinstance(value, str) or not value:
            raise DatasetSchemaError(f"{path}[{index}] must be a non-empty string")
        result.append(value)
    return result


def _optional_string_list(record: Mapping[str, object], key: str, path: str) -> list[str]:
    if key not in record:
        return []
    return _string_list(_as_list(record[key], f"{path}.{key}"), f"{path}.{key}")
