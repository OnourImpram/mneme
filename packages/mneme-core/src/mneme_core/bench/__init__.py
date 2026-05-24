"""Benchmark helpers shared by the ``benchmarks/`` runner scripts.

Three focused submodules:

* :mod:`mneme_core.bench.metrics` - information-retrieval metric primitives
  (nDCG@k, Recall@k, MRR) plus quantile helpers for latency distributions.
* :mod:`mneme_core.bench.synth` - deterministic synthetic corpus and query
  generator. Tests and benchmarks both lean on this so they exercise the
  same shape of data.
* :mod:`mneme_core.bench.hardware` - hardware/runtime probe that emits
  ``hardware.json`` next to every benchmark result for reproducibility.

The submodules are intentionally small and side-effect-free. Bench scripts
own their own argparse, output, and CI guards.
"""

from .hardware import HardwareSnapshot, capture_hardware
from .metrics import (
    mean_reciprocal_rank,
    ndcg_at_k,
    percentiles,
    recall_at_k,
)
from .synth import (
    SyntheticCorpus,
    SyntheticQuery,
    build_synthetic_corpus,
)

__all__ = [
    "HardwareSnapshot",
    "SyntheticCorpus",
    "SyntheticQuery",
    "build_synthetic_corpus",
    "capture_hardware",
    "mean_reciprocal_rank",
    "ndcg_at_k",
    "percentiles",
    "recall_at_k",
]
