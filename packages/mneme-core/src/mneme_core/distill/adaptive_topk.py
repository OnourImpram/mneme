"""Context-window-aware retrieval count.

A vault search injects between ``min_k`` and ``max_k`` hits, but the
right number depends on how much context window is already in use.
Five thousand tokens used can tolerate ten hits; fifty thousand tokens
should not see more than three. ``adaptive_topk`` makes that decision
deterministically from a single integer input.

The interpolation is linear and clamped at both ends:

* ``current_tokens <= low_usage_threshold`` → ``max_k``
* ``current_tokens >= high_usage_threshold`` → ``min_k``
* In between, linearly interpolated and rounded.

Operators tune the four constants per-deployment via ``TopKPolicy``.
The defaults are tuned for Claude Code with a 200k context window:
five thousand tokens is a fresh session, fifty thousand is mid-task.
"""

from __future__ import annotations

from dataclasses import dataclass

DEFAULT_MIN_K = 3
DEFAULT_MAX_K = 10
DEFAULT_LOW_USAGE_THRESHOLD = 5_000
DEFAULT_HIGH_USAGE_THRESHOLD = 50_000


@dataclass
class TopKPolicy:
    """Tunable parameters for the adaptive top-k interpolation."""

    min_k: int = DEFAULT_MIN_K
    max_k: int = DEFAULT_MAX_K
    low_usage_threshold: int = DEFAULT_LOW_USAGE_THRESHOLD
    high_usage_threshold: int = DEFAULT_HIGH_USAGE_THRESHOLD

    def __post_init__(self) -> None:
        if self.min_k > self.max_k:
            raise ValueError("min_k must be <= max_k")
        if self.low_usage_threshold >= self.high_usage_threshold:
            raise ValueError(
                "low_usage_threshold must be strictly less than high_usage_threshold"
            )


def adaptive_topk(
    current_tokens: int, policy: TopKPolicy | None = None
) -> int:
    """Return the top-k value appropriate for the current context usage.

    Negative ``current_tokens`` is treated as zero. Clamping happens
    at both ends so the result is always in ``[policy.min_k, policy.max_k]``.
    """
    p = policy or TopKPolicy()
    n = max(0, int(current_tokens))
    if n <= p.low_usage_threshold:
        return p.max_k
    if n >= p.high_usage_threshold:
        return p.min_k
    span = p.high_usage_threshold - p.low_usage_threshold
    progress = (n - p.low_usage_threshold) / span
    interpolated = p.max_k - progress * (p.max_k - p.min_k)
    return int(round(interpolated))
