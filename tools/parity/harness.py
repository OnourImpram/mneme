"""Top-level parity-check entry point.

Run via the CLI in ``tools/parity_check.py`` or programmatically by
calling :func:`run_parity_check` directly.
"""

from __future__ import annotations

from pathlib import Path

from tools.parity.adapter import ParityAdapter
from tools.parity.metrics import (
    kendall_tau,
    parity_summary,
    top_k_jaccard,
    top_n_agreement,
)


def run_parity_check(
    *,
    mneme: ParityAdapter,
    ground_truth: ParityAdapter,
    queries: list[dict[str, str]],
    top_k: int = 10,
    divergence_threshold: float = 0.6,
) -> dict[str, object]:
    """Drive both adapters through ``queries`` and return parity metrics.

    ``queries`` is a list of dicts shaped ``{"id": str, "text": str}``.
    Adapters are expected to be already-configured (vault indexed for
    mneme, backing store ready for ground truth).
    """
    mneme_status = mneme.status()
    gt_status = ground_truth.status()
    if not mneme_status.available:
        return {
            "comparable": False,
            "reason": f"mneme adapter unavailable: {mneme_status.reason}",
            "mneme_status": _status_to_dict(mneme_status),
            "ground_truth_status": _status_to_dict(gt_status),
        }
    if not gt_status.available:
        return {
            "comparable": False,
            "reason": f"ground-truth adapter unavailable: {gt_status.reason}",
            "mneme_status": _status_to_dict(mneme_status),
            "ground_truth_status": _status_to_dict(gt_status),
        }

    per_query: list[dict[str, object]] = []
    for q in queries:
        qid = q.get("id") or q.get("qid")
        text = q.get("text", "")
        if not qid or not text:
            continue
        m_hits = mneme.search(text, top_k=top_k)
        g_hits = ground_truth.search(text, top_k=top_k)
        m_ids = [h.doc_id for h in m_hits]
        g_ids = [h.doc_id for h in g_hits]
        per_query.append(
            {
                "qid": qid,
                "mneme_top_ids": m_ids[:top_k],
                "ground_truth_top_ids": g_ids[:top_k],
                "top_1_agreement": top_n_agreement(m_ids, g_ids, n=1),
                "top_5_jaccard": top_k_jaccard(m_ids, g_ids, k=5),
                "kendall_tau": kendall_tau(m_ids, g_ids, k=top_k),
            }
        )

    summary = parity_summary(
        per_query, divergence_threshold=divergence_threshold
    )

    return {
        "comparable": True,
        "queries_evaluated": len(per_query),
        "top_k": top_k,
        "mneme_status": _status_to_dict(mneme_status),
        "ground_truth_status": _status_to_dict(gt_status),
        "per_query": per_query,
        "summary": summary,
    }


def _status_to_dict(s: object) -> dict[str, object]:
    return {
        "name": getattr(s, "name", "unknown"),
        "available": getattr(s, "available", False),
        "reason": getattr(s, "reason", ""),
        "extras": getattr(s, "extras", {}),
    }


def load_queries_from_json(path: Path) -> list[dict[str, str]]:
    """Read a JSON file containing ``[{"id": str, "text": str}, ...]``."""
    import json

    with path.open(encoding="utf-8") as f:
        payload = json.load(f)
    if not isinstance(payload, list):
        raise ValueError(f"expected JSON array at {path}, got {type(payload).__name__}")
    out: list[dict[str, str]] = []
    for i, row in enumerate(payload):
        if not isinstance(row, dict):
            continue
        qid = str(row.get("id") or row.get("qid") or f"q{i:03d}")
        text = str(row.get("text", ""))
        if text:
            out.append({"id": qid, "text": text})
    return out
