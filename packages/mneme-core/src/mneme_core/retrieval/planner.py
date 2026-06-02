"""Rule-based retrieval query planner.

Decides which retrieval backends to activate for a given query.
No LLM call. No network. No import of anthropic/openai/requests/httpx.

The decision table is:
- If query stripped length < min_query_length OR token count < min_query_words: return [].
- Always include 'fts5'.
- Include 'dense' only if 'dense' in available_backends AND config.dense_enabled.
- Include 'kg' only if 'kg' in available_backends AND config.kg_enabled.

Returns an ordered list of backend names to activate.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class RetrievalConfig:
    """Configuration for the query planner.

    Attributes:
        min_query_length: minimum stripped character length for the query
            to pass the gate (default 3).
        min_query_words: minimum number of whitespace-separated tokens
            required to pass the gate (default 1).
        dense_enabled: whether the dense retrieval backend is enabled in
            config. Even when True, 'dense' is only activated if it is
            also present in available_backends (default True).
        kg_enabled: whether the KG retrieval backend is enabled in config.
            Even when True, 'kg' is only activated if it is also present
            in available_backends (default True).
    """

    min_query_length: int = 3
    min_query_words: int = 1
    dense_enabled: bool = True
    kg_enabled: bool = True


def plan_retrieval(
    query: str,
    config: RetrievalConfig,
    available_backends: frozenset[str],
) -> list[str]:
    """Decide which backends to activate for a query.

    Args:
        query: the raw user query string.
        config: planner configuration (gate thresholds + backend flags).
        available_backends: set of backend names that are wired and
            available at runtime (e.g. frozenset({"fts5", "dense"})).

    Returns:
        Ordered list of backend names to activate. Empty list when the
        query fails the gate.
    """
    stripped = query.strip()
    tokens = stripped.split()
    if len(stripped) < config.min_query_length or len(tokens) < config.min_query_words:
        return []

    backends: list[str] = ["fts5"]

    if config.dense_enabled and "dense" in available_backends:
        backends.append("dense")

    if config.kg_enabled and "kg" in available_backends:
        backends.append("kg")

    return backends
