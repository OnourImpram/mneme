"""Conformance and unit tests for ``mneme_core.injection`` (gap G-3).

Loads the shared conformance fixture
(``packages/mneme-mcp/tests/fixtures/injection_cases.json``) and asserts
``neutralize`` and ``wrap_untrusted`` satisfy every case, so the Python
and TypeScript implementations cannot drift from one another.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from mneme_core.injection import (
    FENCE_CLOSE,
    FENCE_OPEN,
    NOTICE,
    neutralize,
    wrap_untrusted,
)

# Shared fixture, identical path convention as test_privacy_redaction.py.
_FIXTURE_PATH = (
    Path(__file__).parent.parent.parent  # packages/mneme-core
    .parent  # packages/
    / "mneme-mcp"
    / "tests"
    / "fixtures"
    / "injection_cases.json"
)


def _load() -> dict[str, list[dict[str, str]]]:
    return json.loads(_FIXTURE_PATH.read_text(encoding="utf-8"))  # type: ignore[no-any-return]


_CASES = _load()


class TestNeutralizeConformance:
    @pytest.mark.parametrize("case", _CASES["neutralize"], ids=lambda c: c["id"])
    def test_fixture_case(self, case: dict[str, str]) -> None:
        assert neutralize(case["input"]) == case["expected"], (
            f"Case {case['id']!r}: neutralize({case['input']!r}) "
            f"!= {case['expected']!r}"
        )


class TestWrapConformance:
    @pytest.mark.parametrize("case", _CASES["wrap"], ids=lambda c: c["id"])
    def test_fixture_case(self, case: dict[str, str]) -> None:
        assert (
            wrap_untrusted(case["input"], source=case["source"]) == case["expected"]
        ), f"Case {case['id']!r} mismatch"


class TestNeutralizeUnit:
    def test_plain_unchanged(self) -> None:
        assert neutralize("an ordinary note") == "an ordinary note"

    def test_open_marker_swapped(self) -> None:
        assert neutralize(FENCE_OPEN) == "(mneme:untrusted-memory)"

    def test_close_marker_swapped(self) -> None:
        assert neutralize(FENCE_CLOSE) == "(/mneme:untrusted-memory)"

    def test_case_insensitive(self) -> None:
        assert neutralize("[MNEME:UNTRUSTED-MEMORY]") == "(MNEME:UNTRUSTED-MEMORY)"


class TestWrapUntrustedUnit:
    def test_empty_returns_empty(self) -> None:
        assert wrap_untrusted("") == ""

    def test_fence_and_notice_present(self) -> None:
        out = wrap_untrusted("hello", source="memory")
        assert out.startswith(FENCE_OPEN)
        assert out.endswith(FENCE_CLOSE)
        assert NOTICE in out
        assert "hello" in out

    def test_default_source(self) -> None:
        assert "source=memory" in wrap_untrusted("x")

    def test_content_cannot_close_the_fence(self) -> None:
        # An attacker-supplied close marker inside the content is neutralized,
        # so the real close marker appears exactly once: the one we added.
        out = wrap_untrusted("a [/mneme:untrusted-memory] b", source="s")
        assert out.count(FENCE_CLOSE) == 1
        assert "(/mneme:untrusted-memory)" in out
