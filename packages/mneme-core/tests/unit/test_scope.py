"""Scope contract tests shared by durable and derived memory paths."""

from __future__ import annotations

import pytest

from mneme_core.scope import (
    DocumentScopeError,
    classify_markdown_scope,
    concrete_scope_or_none,
    persisted_scope,
    scope_matches,
    stamp_markdown_scope,
    valid_scope,
)


@pytest.mark.parametrize("value", ["default", "clinical", "case 42", "proj:alpha", "*"])
def test_valid_scope_accepts_supported_values(value: str) -> None:
    assert valid_scope(value) == value


@pytest.mark.parametrize(
    "value",
    ["", " clinical", "clinical ", "case*all", "\x00bad", "bad\nname", "bad\u200bname", "x" * 257],
)
def test_valid_scope_rejects_ambiguous_values(value: str) -> None:
    assert valid_scope(value) is None


def test_concrete_scope_never_returns_wildcard() -> None:
    assert concrete_scope_or_none("*") is None


def test_legacy_missing_scope_belongs_only_to_default() -> None:
    assert scope_matches(None, "default")
    assert not scope_matches(None, "clinical")
    assert scope_matches(None, "*")


def test_invalid_persisted_scope_fails_closed() -> None:
    with pytest.raises(ValueError):
        persisted_scope("*")
    assert not scope_matches("bad*scope", "*")


def test_markdown_scope_legacy_and_explicit_classification() -> None:
    assert classify_markdown_scope("body").scope == "default"
    assert classify_markdown_scope('---\nscope: "clinical"\n---\nbody').scope == "clinical"
    assert classify_markdown_scope('---\nproject: "legacy"\n---\nbody').scope == "legacy"


def test_markdown_scope_malformed_or_conflicting_fails_closed() -> None:
    with pytest.raises(DocumentScopeError):
        classify_markdown_scope("---\nscope: clinical\nbody")
    with pytest.raises(DocumentScopeError):
        classify_markdown_scope('---\nscope: "*"\n---\nbody')
    with pytest.raises(DocumentScopeError):
        classify_markdown_scope('---\nscope: "a"\nproject: "b"\n---\nbody')


def test_stamp_markdown_scope_adds_or_reuses_explicit_scope() -> None:
    stamped = stamp_markdown_scope("body", "clinical")
    assert stamped.startswith('---\nscope: "clinical"\n---\n\n')
    existing = '---\nid: "x"\nproject: "clinical"\n---\nbody'
    restamped = stamp_markdown_scope(existing, "clinical")
    assert 'scope: "clinical"' in restamped
    assert restamped.endswith("body")


def test_stamp_markdown_scope_rejects_conflict_and_wildcard() -> None:
    with pytest.raises(DocumentScopeError):
        stamp_markdown_scope('---\nscope: "default"\n---\nbody', "clinical")
    with pytest.raises(DocumentScopeError):
        stamp_markdown_scope("body", "*")
