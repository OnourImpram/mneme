"""Benchmark helpers shared by the ``benchmarks/`` runner scripts.

Four focused surfaces:

* :mod:`mneme_core.bench.metrics` - information-retrieval metric primitives
  (nDCG@k, Recall@k, Precision@k, MRR) plus quantile helpers for latency
  distributions.
* :mod:`mneme_core.bench.synth` - deterministic synthetic corpus and query
  generator. Tests and benchmarks both lean on this so they exercise the
  same shape of data.
* :mod:`mneme_core.bench.hardware` - hardware/runtime probe that emits
  ``hardware.json`` next to every benchmark result for reproducibility.
* :mod:`mneme_core.bench.gate` - seven independently runnable 3.6 benchmark
  surfaces with explicit synthetic provenance and official schema probes.

Imports from this module are intentionally small and side-effect-free. The gate
module owns its argparse and output behavior.
"""

from .hardware import HardwareSnapshot, capture_hardware
from .metrics import (
    mean_reciprocal_rank,
    ndcg_at_k,
    percentiles,
    precision_at_k,
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
    "precision_at_k",
    "recall_at_k",
]
