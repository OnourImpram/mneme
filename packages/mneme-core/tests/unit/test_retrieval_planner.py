"""Unit tests for mneme_core.retrieval.planner.

Decision table: 10 cases covering all branches of plan_retrieval.
"""

from __future__ import annotations

from mneme_core.retrieval.planner import RetrievalConfig, plan_retrieval

_ALL_BACKENDS: frozenset[str] = frozenset({"fts5", "dense", "kg"})
_FTS_ONLY: frozenset[str] = frozenset({"fts5"})
_DENSE_ONLY: frozenset[str] = frozenset({"fts5", "dense"})
_KG_ONLY: frozenset[str] = frozenset({"fts5", "kg"})


def _cfg(**kwargs: object) -> RetrievalConfig:
    defaults: dict[str, object] = {
        "min_query_length": 3,
        "min_query_words": 1,
        "dense_enabled": True,
        "kg_enabled": True,
    }
    defaults.update(kwargs)
    return RetrievalConfig(**defaults)  # type: ignore[arg-type]


# --- Gate tests (returns []) ---


def test_empty_query_returns_empty() -> None:
    """Case 1: stripped length < min_query_length returns []."""
    result = plan_retrieval("", _cfg(), _ALL_BACKENDS)
    assert result == []


def test_too_short_query_returns_empty() -> None:
    """Case 2: query shorter than min_query_length returns []."""
    result = plan_retrieval("hi", _cfg(min_query_length=5), _ALL_BACKENDS)
    assert result == []


def test_too_few_words_returns_empty() -> None:
    """Case 3: query with fewer words than min_query_words returns []."""
    result = plan_retrieval("oneword", _cfg(min_query_words=2), _ALL_BACKENDS)
    assert result == []


def test_whitespace_only_returns_empty() -> None:
    """Case 4: whitespace-only query fails the length gate."""
    result = plan_retrieval("   ", _cfg(), _ALL_BACKENDS)
    assert result == []


# --- fts5 always included ---


def test_fts5_always_included_when_query_passes() -> None:
    """Case 5: any valid query always includes fts5."""
    result = plan_retrieval("a valid query", _cfg(), _FTS_ONLY)
    assert "fts5" in result


def test_fts5_only_when_dense_not_available() -> None:
    """Case 6: dense not in available_backends -> only fts5."""
    result = plan_retrieval("a valid query", _cfg(dense_enabled=True), _FTS_ONLY)
    assert result == ["fts5"]


# --- dense inclusion ---


def test_dense_included_when_available_and_enabled() -> None:
    """Case 7: dense in available_backends and dense_enabled=True -> fts5 + dense."""
    result = plan_retrieval("a valid query", _cfg(dense_enabled=True), _DENSE_ONLY)
    assert "fts5" in result
    assert "dense" in result


def test_dense_excluded_when_config_disabled() -> None:
    """Case 8: dense in available_backends but dense_enabled=False -> only fts5."""
    result = plan_retrieval("a valid query", _cfg(dense_enabled=False), _DENSE_ONLY)
    assert "dense" not in result
    assert "fts5" in result


# --- kg inclusion ---


def test_kg_included_when_available() -> None:
    """Case 9: kg in available_backends and kg_enabled=True -> fts5 + kg."""
    result = plan_retrieval("a valid query", _cfg(kg_enabled=True), _KG_ONLY)
    assert "kg" in result
    assert "fts5" in result


def test_kg_excluded_when_config_disabled() -> None:
    """Case 10: kg in available_backends but kg_enabled=False -> only fts5."""
    result = plan_retrieval("a valid query", _cfg(kg_enabled=False), _KG_ONLY)
    assert "kg" not in result
    assert "fts5" in result


# --- no LLM / no network import guard ---


def test_no_llm_import_in_planner() -> None:
    """Planner must not import anthropic, openai, requests, or httpx."""
    import mneme_core.retrieval.planner as planner_mod

    for forbidden in ("anthropic", "openai", "requests", "httpx"):
        assert forbidden not in dir(planner_mod), (
            f"planner module must not import {forbidden!r}"
        )


def test_return_is_list_of_strings() -> None:
    """plan_retrieval always returns list[str]."""
    result = plan_retrieval("a valid query", _cfg(), _ALL_BACKENDS)
    assert isinstance(result, list)
    assert all(isinstance(b, str) for b in result)


def test_all_backends_included_when_all_available_and_enabled() -> None:
    """All three backends returned when all available and config enables them."""
    result = plan_retrieval("a valid query", _cfg(), _ALL_BACKENDS)
    assert set(result) == {"fts5", "dense", "kg"}
