"""Test-runner output parser: pytest and unittest console output → FailureMemory.

Parses pytest / unittest console output into :class:`TestFailure` records by
re-using :func:`~mneme_code.stacktrace.parse_traceback` and
:func:`~mneme_code.failure.failure_from_traceback`.  Pure, deterministic,
never raises.

Design invariants (mirror stacktrace.py / failure.py):
* No clock, no random, no I/O inside parsing functions.
* Redaction is inherited from ``parse_traceback`` — every ``ParsedTraceback``
  field is already redacted before it reaches a ``TestFailure`` or
  ``FailureMemory``.
* ``failures_from_test_output`` accepts ``observed_at: datetime`` injected by
  the caller; it never calls ``datetime.now()``.
* Never raises — every public entry point catches all exceptions and returns
  ``[]``.
* Frozen dataclasses for all public types.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime

from mneme_core.privacy import redact

from mneme_code.failure import FailureMemory, failure_from_traceback
from mneme_code.stacktrace import ParsedTraceback, parse_traceback

# ---------------------------------------------------------------------------
# Regex patterns
# ---------------------------------------------------------------------------

# pytest: separator banner around each failing test block.
# e.g. "________ TestFoo.test_bar ________" or "________ test_bar ________"
_PYTEST_BANNER = re.compile(r"^_{4,}\s+(.+?)\s+_{4,}\s*$", re.MULTILINE)

# pytest: failures section header
_PYTEST_FAILURES_HDR = re.compile(r"=+\s*FAILURES\s*=+", re.IGNORECASE)

# pytest: short-test-summary lines  ("FAILED tests/x.py::C::m - ...")
# Requires the test ID to look like a path (contains "/" or "::") so bare
# unittest footers like "FAILED (failures=1)" are not matched.
_PYTEST_SUMMARY_LINE = re.compile(r"^FAILED\s+(\S*(?:/|::)\S*)", re.MULTILINE)

# unittest: "FAIL: test_x (mod.TestClass)" or "ERROR: test_x (mod.TestClass)"
_UNITTEST_HDR = re.compile(
    r"^(?:FAIL|ERROR):\s+(.+?)\s*$",
    re.MULTILINE,
)

# Rule separators used by unittest (lines of "=" or "-", >= 10 chars)
_RULE_LINE = re.compile(r"^[=\-]{10,}\s*$", re.MULTILINE)

# Traceback start sentinel
_TB_START = re.compile(r"Traceback \(most recent call last\):", re.MULTILINE)


# ---------------------------------------------------------------------------
# Dataclass
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TestFailure:
    """A single test failure with optional parsed traceback.

    Attributes:
        test_id    Human-readable test identifier.
                   pytest style:    ``"tests/test_x.py::TestY::test_z"``
                   unittest style:  ``"test_z (mod.TestY)"``
        traceback  :class:`~mneme_code.stacktrace.ParsedTraceback` parsed from
                   the failure's traceback block, or ``None`` if no parseable
                   traceback was found.
    """

    test_id: str
    traceback: ParsedTraceback | None


# ---------------------------------------------------------------------------
# pytest parser
# ---------------------------------------------------------------------------


def parse_pytest_output(text: str) -> list[TestFailure]:
    """Parse *text* as pytest console output into :class:`TestFailure` records.

    Strategy:
    1. Locate the ``=== FAILURES ===`` section (or fall back to the full text).
    2. Use ``_______ <test-id> _______`` banners to delimit per-test blocks.
    3. For each block, call :func:`~mneme_code.stacktrace.parse_traceback` on
       the block text.
    4. Fall back to short-test-summary ``FAILED ...`` lines for test IDs that
       had no banner-delimited block.

    Args:
        text: Raw pytest stdout/stderr output.

    Returns:
        Ordered list of :class:`TestFailure`, one per failing test.
        Returns ``[]`` if nothing parseable is found or on any error.
    """
    try:
        return _parse_pytest_inner(text)
    except Exception:  # noqa: BLE001
        return []


def _parse_pytest_inner(text: str) -> list[TestFailure]:
    """Inner pytest parser — may raise; wrapped by ``parse_pytest_output``."""
    if not text or not text.strip():
        return []

    # Locate the FAILURES section; restrict parsing to that region when present.
    hdr_match = _PYTEST_FAILURES_HDR.search(text)
    body = text[hdr_match.start() :] if hdr_match else text

    # Split on banner lines to get (banner_text, block_text) pairs.
    banners = list(_PYTEST_BANNER.finditer(body))
    if not banners:
        # No banner-delimited blocks; try summary lines only (no tracebacks).
        return _pytest_summary_fallback(text)

    failures: list[TestFailure] = []
    for idx, banner in enumerate(banners):
        # B5: redact the test_id before it becomes source_label. A parametrized
        # test name can carry secret-like text (e.g. test_x[<private>tok</private>]);
        # failure.py assumes source_label is already clean, so redact at the source.
        test_id = redact(banner.group(1).strip())
        block_start = banner.end()
        block_end = banners[idx + 1].start() if idx + 1 < len(banners) else len(body)
        block_text = body[block_start:block_end]
        parsed = parse_traceback(block_text)
        failures.append(TestFailure(test_id=test_id, traceback=parsed))

    return failures


def _pytest_summary_fallback(text: str) -> list[TestFailure]:
    """Return TestFailures from FAILED summary lines when no banners exist."""
    return [
        TestFailure(test_id=redact(m.group(1)), traceback=None)
        for m in _PYTEST_SUMMARY_LINE.finditer(text)
    ]


# ---------------------------------------------------------------------------
# unittest parser
# ---------------------------------------------------------------------------


def parse_unittest_output(text: str) -> list[TestFailure]:
    """Parse *text* as unittest console output into :class:`TestFailure` records.

    Strategy:
    1. Find ``FAIL:`` / ``ERROR:`` header lines.
    2. For each header, extract the block that follows (from the next rule line
       to the subsequent rule line or the next FAIL/ERROR header).
    3. Within that block, locate the ``Traceback (most recent call last):`` and
       parse it with :func:`~mneme_code.stacktrace.parse_traceback`.

    Args:
        text: Raw unittest stdout/stderr output.

    Returns:
        Ordered list of :class:`TestFailure`.
        Returns ``[]`` if nothing parseable is found or on any error.
    """
    try:
        return _parse_unittest_inner(text)
    except Exception:  # noqa: BLE001
        return []


def _parse_unittest_inner(text: str) -> list[TestFailure]:
    """Inner unittest parser — may raise; wrapped by ``parse_unittest_output``."""
    if not text or not text.strip():
        return []

    hdrs = list(_UNITTEST_HDR.finditer(text))
    if not hdrs:
        return []

    failures: list[TestFailure] = []
    for idx, hdr in enumerate(hdrs):
        # B5: redact the test_id before it becomes source_label (see parse_pytest).
        test_id = redact(hdr.group(1).strip())
        block_start = hdr.end()
        # Block ends at the start of the next header or end of text.
        block_end = hdrs[idx + 1].start() if idx + 1 < len(hdrs) else len(text)
        block_text = text[block_start:block_end]
        parsed = parse_traceback(block_text)
        failures.append(TestFailure(test_id=test_id, traceback=parsed))

    return failures


# ---------------------------------------------------------------------------
# Unified entry point
# ---------------------------------------------------------------------------


def failures_from_test_output(
    text: str,
    *,
    observed_at: datetime,
    runner: str = "auto",
) -> list[FailureMemory]:
    """Convert test-runner console output into :class:`FailureMemory` records.

    Auto-detection heuristic (``runner="auto"``):
    * pytest  — text contains ``"=== FAILURES ==="`` (case-insensitive) *or*
                ``"short test summary"`` *or* a ``"FAILED "`` summary line *or*
                a ``"______"`` banner line.
    * unittest — text contains a ``"FAIL:"`` / ``"ERROR:"`` header followed by
                 a traceback block.
    * Falls back to pytest if detection is ambiguous.

    Only :class:`TestFailure` records whose ``.traceback`` is not ``None`` are
    converted; failures with no parseable traceback are silently skipped.

    Args:
        text:        Raw test-runner output.
        observed_at: Tz-aware UTC datetime for each :class:`FailureMemory`.
                     Must be injected by the caller; this function never calls
                     ``datetime.now()``.
        runner:      ``"pytest"``, ``"unittest"``, or ``"auto"`` (default).

    Returns:
        List of :class:`FailureMemory`, one per test with a parseable
        traceback.  Returns ``[]`` on any error or if nothing is parseable.
    """
    try:
        return _failures_inner(text, observed_at=observed_at, runner=runner)
    except Exception:  # noqa: BLE001
        return []


def _failures_inner(
    text: str,
    *,
    observed_at: datetime,
    runner: str,
) -> list[FailureMemory]:
    """Inner implementation — may raise; wrapped by ``failures_from_test_output``."""
    if not text or not text.strip():
        return []

    effective_runner = runner if runner != "auto" else _detect_runner(text)

    if effective_runner == "unittest":
        test_failures = parse_unittest_output(text)
    else:
        test_failures = parse_pytest_output(text)

    results: list[FailureMemory] = []
    for tf in test_failures:
        if tf.traceback is None:
            continue
        fm = failure_from_traceback(
            tf.traceback,
            observed_at=observed_at,
            source_label=tf.test_id,
        )
        results.append(fm)

    return results


def _detect_runner(text: str) -> str:
    """Return ``"pytest"`` or ``"unittest"`` based on text heuristics.

    Unittest signals are checked first because they are more specific
    (``FAIL:``/``ERROR:`` headers are unambiguous).  Pytest signals that
    can appear in unittest output (e.g. the word ``FAILED``) are only
    consulted when the unittest pattern is absent.
    """
    # unittest signals: FAIL:/ERROR: header + traceback — check first, more specific
    if _UNITTEST_HDR.search(text) and _TB_START.search(text):
        return "unittest"
    # pytest signals
    lower = text.lower()
    if (
        "=== failures ===" in lower
        or "short test summary" in lower
        or _PYTEST_SUMMARY_LINE.search(text)
        or _PYTEST_BANNER.search(text)
    ):
        return "pytest"
    # Default to pytest
    return "pytest"
