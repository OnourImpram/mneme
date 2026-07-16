"""Regression tests for the tree-sitter native ABI guard."""

from __future__ import annotations

import pytest

from mneme_graph.extractor._tree_sitter_compat import (
    _major_minor,
    assert_tree_sitter_compatible,
)


def test_supported_tree_sitter_release_is_accepted() -> None:
    assert_tree_sitter_compatible("0.25.2")


@pytest.mark.parametrize("raw", ["0.26.0", "0.27.1", "1.0.0"])
def test_unsupported_new_binding_fails_before_native_parse(raw: str) -> None:
    with pytest.raises(RuntimeError, match=r"requires tree-sitter>=0\.25,<0\.26"):
        assert_tree_sitter_compatible(raw)


def test_unsupported_old_binding_is_rejected() -> None:
    with pytest.raises(RuntimeError, match="Reinstall mneme-graph"):
        assert_tree_sitter_compatible("0.24.7")


def test_malformed_version_fails_closed() -> None:
    with pytest.raises(RuntimeError, match="Unsupported tree-sitter version string"):
        _major_minor("development")
