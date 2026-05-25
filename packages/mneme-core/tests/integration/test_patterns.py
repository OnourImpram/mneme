"""Integration tests for the reusable action-pattern memory module."""

from __future__ import annotations

from pathlib import Path

import pytest

from mneme_core.patterns import (
    PATTERN_TYPE,
    Pattern,
    _slug,
    delete_pattern,
    from_markdown,
    list_patterns,
    load_pattern,
    search_patterns,
    store_pattern,
    to_markdown,
)
from mneme_core.vault.config import VaultConfig


@pytest.fixture
def vault(tmp_path: Path) -> VaultConfig:
    v = VaultConfig.from_path(tmp_path)
    (v.root / ".mneme").mkdir(parents=True, exist_ok=True)
    return v


def _sample(name: str = "use-rrf-for-mixed-text") -> Pattern:
    return Pattern(
        name=name,
        signal="user query mixes English plus Turkish casefold targets",
        action="run mneme_search with RRF k=60 across FTS5 plus dense backends",
        outcome="top-1 nDCG @ 5 improves vs single backend",
        tags=["retrieval", "rrf"],
    )


class TestSerialization:
    def test_round_trip_markdown(self) -> None:
        original = _sample()
        text = to_markdown(original)
        # Frontmatter is present.
        assert text.startswith("---\n")
        assert f"type: {PATTERN_TYPE}" in text
        parsed = from_markdown(text)
        assert parsed is not None
        assert parsed.name == original.name
        assert parsed.signal == original.signal
        assert parsed.action == original.action
        assert parsed.outcome == original.outcome
        assert parsed.tags == original.tags

    def test_parse_returns_none_for_non_pattern(self) -> None:
        text = "---\ntype: other\n---\n\n## Section\n"
        assert from_markdown(text) is None

    def test_parse_returns_none_for_malformed(self) -> None:
        assert from_markdown("no frontmatter here") is None


class TestStoreAndLoad:
    def test_store_creates_file(self, vault: VaultConfig) -> None:
        path = store_pattern(vault, _sample())
        assert path.is_file()
        assert path.parent == vault.patterns_dir

    def test_load_recovers_fields(self, vault: VaultConfig) -> None:
        original = _sample()
        store_pattern(vault, original)
        loaded = load_pattern(vault, original.name)
        assert loaded is not None
        assert loaded.signal == original.signal
        assert loaded.tags == original.tags

    def test_missing_returns_none(self, vault: VaultConfig) -> None:
        assert load_pattern(vault, "never-existed") is None

    def test_store_is_idempotent_under_same_name(self, vault: VaultConfig) -> None:
        p = _sample()
        store_pattern(vault, p)
        store_pattern(vault, p)  # overwrites
        files = list(vault.patterns_dir.glob("*.md"))
        assert len(files) == 1

    def test_unsafe_name_sanitized(self, vault: VaultConfig) -> None:
        p = Pattern(name="../../etc/passwd", signal="x", action="y")
        path = store_pattern(vault, p)
        # File lives under patterns_dir.
        assert path.parent == vault.patterns_dir
        assert ".." not in path.name


class TestListAndDelete:
    def test_list_empty(self, vault: VaultConfig) -> None:
        assert list_patterns(vault) == []

    def test_list_sorts_deterministically(self, vault: VaultConfig) -> None:
        for n in ["alpha", "gamma", "beta"]:
            store_pattern(vault, Pattern(name=n, signal="s", action="a"))
        names = [p.name for p in list_patterns(vault)]
        assert sorted(names) == names

    def test_delete_removes_file(self, vault: VaultConfig) -> None:
        store_pattern(vault, _sample())
        assert delete_pattern(vault, "use-rrf-for-mixed-text") is True
        assert load_pattern(vault, "use-rrf-for-mixed-text") is None

    def test_delete_missing_returns_false(self, vault: VaultConfig) -> None:
        assert delete_pattern(vault, "never") is False


class TestSearch:
    def test_finds_by_signal_token(self, vault: VaultConfig) -> None:
        store_pattern(vault, _sample())
        hits = search_patterns(vault, "Turkish casefold")
        assert len(hits) == 1

    def test_finds_by_tag(self, vault: VaultConfig) -> None:
        store_pattern(vault, _sample())
        hits = search_patterns(vault, "retrieval")
        assert len(hits) == 1

    def test_no_match_returns_empty(self, vault: VaultConfig) -> None:
        store_pattern(vault, _sample())
        assert search_patterns(vault, "unrelatedxyz") == []

    def test_empty_query_returns_empty(self, vault: VaultConfig) -> None:
        store_pattern(vault, _sample())
        assert search_patterns(vault, "") == []
        assert search_patterns(vault, "   ") == []

    def test_top_k_truncates(self, vault: VaultConfig) -> None:
        for i in range(5):
            store_pattern(
                vault,
                Pattern(
                    name=f"pattern-{i}",
                    signal="common-word",
                    action="a",
                ),
            )
        hits = search_patterns(vault, "common-word", top_k=3)
        assert len(hits) == 3

    def test_score_reflects_token_count(self, vault: VaultConfig) -> None:
        store_pattern(
            vault,
            Pattern(
                name="multi-match",
                signal="alpha beta gamma",
                action="a",
                outcome="b",
            ),
        )
        store_pattern(
            vault,
            Pattern(
                name="one-match",
                signal="alpha only",
                action="a",
            ),
        )
        hits = search_patterns(vault, "alpha beta")
        # multi-match scores higher.
        assert hits[0].pattern.name == "multi-match"


class TestSlugDotCollapse:
    """Dot-run collapse: '..' must never survive in a slug."""

    def test_double_dot_alone(self) -> None:
        result = _slug("..")
        assert ".." not in result

    def test_traversal_path_collapsed(self) -> None:
        # "a/../../b" -> after slash→hyphen: "a-..-..-b" -> dots collapsed
        result = _slug("a/../../b")
        assert ".." not in result

    def test_quad_dots_collapsed(self) -> None:
        result = _slug("....")
        assert ".." not in result

    def test_normal_input_unchanged(self) -> None:
        # A plain slug with no dot-runs must pass through identically.
        assert _slug("use-rrf-for-mixed-text") == "use-rrf-for-mixed-text"

    def test_single_dot_preserved(self) -> None:
        # A single dot between words should survive (e.g. "v1.2").
        result = _slug("v1.2-release")
        assert "." in result
        assert ".." not in result
