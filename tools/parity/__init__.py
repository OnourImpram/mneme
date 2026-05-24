"""Parity-check harness for mneme dogfood week.

This package compares mneme retrieval against any user-supplied
ground-truth memory system on a real query set. It is intentionally
not part of ``benchmarks/`` because benchmarks consume seeded
synthetic corpora while parity checks consume operator's real data.

Public surface:

* :class:`ParityAdapter` - the minimal contract a competitor must
  implement (status + search; migration is assumed pre-done by the
  operator).
* :class:`MnemeParityAdapter` - production mneme adapter that points
  at an existing vault without re-migrating.
* :func:`run_parity_check` - drives both adapters through a query set
  and returns parity metrics.

The ground-truth adapter is loaded by module path so any operator can
plug in their own system without that system appearing in the mneme
source tree.
"""

from tools.parity.adapter import (
    MnemeParityAdapter,
    ParityAdapter,
    ParityAdapterStatus,
    ParityHit,
)
from tools.parity.metrics import (
    kendall_tau,
    parity_summary,
    top_k_jaccard,
    top_n_agreement,
)
from tools.parity.harness import run_parity_check

__all__ = [
    "MnemeParityAdapter",
    "ParityAdapter",
    "ParityAdapterStatus",
    "ParityHit",
    "kendall_tau",
    "parity_summary",
    "run_parity_check",
    "top_k_jaccard",
    "top_n_agreement",
]
