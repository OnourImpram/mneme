"""Shared scope validation and Markdown stamping rules."""

from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass
from typing import Any, Final

import yaml

DEFAULT_SCOPE: Final = "default"
MAX_SCOPE_LENGTH: Final = 256
MAX_FRONTMATTER_BYTES: Final = 64 * 1024
MAX_FRONTMATTER_LINES: Final = 512
_SCOPE_RE: Final = re.compile(
    r"^(?:\*|[^\s*\x00-\x1f\x7f](?:[^*\x00-\x1f\x7f]*[^\s*\x00-\x1f\x7f])?)$"
)


class DocumentScopeError(ValueError):
    """Raised when Markdown scope metadata is malformed or contradictory."""


@dataclass(frozen=True)
class MarkdownScope:
    """Scope classification and frontmatter location for a Markdown document."""

    scope: str
    has_frontmatter: bool
    has_explicit_scope: bool
    closing_offset: int | None
    newline: str


def valid_scope(value: object, *, allow_wildcard: bool = True) -> str | None:
    """Return an unchanged valid scope, otherwise ``None``."""
    if not isinstance(value, str) or not 1 <= len(value) <= MAX_SCOPE_LENGTH:
        return None
    if _SCOPE_RE.fullmatch(value) is None:
        return None
    if any(unicodedata.category(char) in {"Cc", "Cf", "Zl", "Zp"} for char in value):
        return None
    if value == "*" and not allow_wildcard:
        return None
    return value


def concrete_scope_or_none(value: object) -> str | None:
    """Return a valid concrete scope, never the wildcard."""
    return valid_scope(value, allow_wildcard=False)


def persisted_scope(value: object | None) -> str:
    """Map missing legacy metadata to default and reject invalid values."""
    if value is None or value == "":
        return DEFAULT_SCOPE
    scope = concrete_scope_or_none(value)
    if scope is None:
        raise ValueError("persisted scope must be a concrete valid identifier")
    return scope


def scope_matches(record_scope: object | None, requested_scope: str) -> bool:
    """Return whether a persisted record is visible in a requested scope."""
    requested = valid_scope(requested_scope)
    if requested is None:
        return False
    try:
        persisted = persisted_scope(record_scope)
    except ValueError:
        return False
    return requested == "*" or persisted == requested


def _frontmatter_parts(text: str) -> tuple[str, int, str] | None:
    lines = text.splitlines(keepends=True)
    if not lines or lines[0].strip() != "---":
        return None
    newline = "\r\n" if lines[0].endswith("\r\n") else "\n"
    consumed = len(lines[0].encode("utf-8"))
    yaml_lines: list[str] = []
    for index, line in enumerate(lines[1 : MAX_FRONTMATTER_LINES + 1], start=1):
        if line.strip() == "---":
            closing_offset = sum(len(item) for item in lines[:index])
            return "".join(yaml_lines), closing_offset, newline
        yaml_lines.append(line)
        consumed += len(line.encode("utf-8"))
        if consumed > MAX_FRONTMATTER_BYTES:
            raise DocumentScopeError("frontmatter exceeds the safe parsing bound")
    raise DocumentScopeError("frontmatter is not closed within the safe parsing bound")


def _load_yaml_block(yaml_block: str) -> Any:
    from .vault.frontmatter import load_yaml_block

    return load_yaml_block(yaml_block)


def classify_markdown_scope(text: str) -> MarkdownScope:
    """Classify a Markdown document without widening malformed metadata."""
    parts = _frontmatter_parts(text)
    if parts is None:
        return MarkdownScope(DEFAULT_SCOPE, False, False, None, "\n")
    yaml_block, closing_offset, newline = parts
    try:
        loaded = _load_yaml_block(yaml_block) or {}
    except yaml.YAMLError as exc:
        raise DocumentScopeError("frontmatter YAML is malformed") from exc
    if not isinstance(loaded, dict):
        raise DocumentScopeError("frontmatter must be a mapping")
    data: dict[str, Any] = loaded
    has_explicit = "scope" in data or "project" in data
    if "scope" in data and "project" in data and data["scope"] != data["project"]:
        raise DocumentScopeError("scope and legacy project metadata conflict")
    candidate = data.get("scope", data.get("project", DEFAULT_SCOPE))
    try:
        scope = persisted_scope(candidate)
    except ValueError as exc:
        raise DocumentScopeError("frontmatter scope is invalid") from exc
    return MarkdownScope(scope, True, has_explicit, closing_offset, newline)


def stamp_markdown_scope(text: str, requested_scope: str) -> str:
    """Return Markdown explicitly bound to a concrete requested scope."""
    scope = concrete_scope_or_none(requested_scope)
    if scope is None:
        raise DocumentScopeError("durable writes require a concrete valid scope")
    classified = classify_markdown_scope(text)
    if classified.has_explicit_scope and classified.scope != scope:
        raise DocumentScopeError("document scope does not match the proposal scope")
    if classified.has_frontmatter:
        if classified.closing_offset is None:
            raise DocumentScopeError("frontmatter closing offset is unavailable")
        parts = _frontmatter_parts(text)
        assert parts is not None
        yaml_block, closing_offset, newline = parts
        try:
            loaded = _load_yaml_block(yaml_block) or {}
        except yaml.YAMLError as exc:
            raise DocumentScopeError("frontmatter YAML is malformed") from exc
        if isinstance(loaded, dict) and "scope" in loaded:
            return text
        insertion = f"scope: {json.dumps(scope, ensure_ascii=False)}{newline}"
        return text[:closing_offset] + insertion + text[closing_offset:]
    newline = classified.newline
    return (
        f"---{newline}scope: {json.dumps(scope, ensure_ascii=False)}{newline}"
        f"---{newline}{newline}{text}"
    )
