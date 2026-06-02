"""LongMemEval evaluation harness for mneme retrieval.

Measures Recall@1/5/10 and MRR@10 on the LongMemEval dataset format.
Works with only the FTS5 leg active (no dense adapter required).
Supports both the official dataset format and the synthetic fixture
format (same schema) for CI-safe runs.

Usage with synthetic fixture (CI-safe, no download required):

    python -m benchmarks.longmemeval.runner

Usage with the real dataset:

    python -m benchmarks.longmemeval.runner --dataset path/to/longmemeval.json

Write baseline (synthetic fixture):

    python -m benchmarks.longmemeval.runner --write-baseline
"""
