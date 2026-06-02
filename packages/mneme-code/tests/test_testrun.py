"""Tests for mneme_code.testrun: pytest/unittest output parsing → FailureMemory."""

from __future__ import annotations

from datetime import UTC, datetime

from mneme_code.testrun import (
    failures_from_test_output,
    parse_pytest_output,
    parse_unittest_output,
)

# ---------------------------------------------------------------------------
# Sample fixtures
# ---------------------------------------------------------------------------

# A realistic pytest FAILURES block with one failing test and a 2-frame
# traceback.  The exception message embeds a private token to verify
# redaction flows through parse_traceback → FailureMemory.
_PYTEST_OUTPUT = """\
============================= test session results ==============================
collected 3 items

tests/test_app.py::TestSuite::test_compute PASSED
tests/test_app.py::TestSuite::test_process FAILED

=================================== FAILURES ===================================
_______ TestSuite.test_process _______

Traceback (most recent call last):
  File "tests/test_app.py", line 42, in test_process
    result = process(payload)
  File "src/app.py", line 17, in process
    raise RuntimeError("<private>tok</private>")
RuntimeError: <private>tok</private>

=========================== short test summary info ============================
FAILED tests/test_app.py::TestSuite::test_process - RuntimeError
========================= 1 failed, 2 passed in 0.12s ==========================
"""

# A unittest output block with one FAIL entry and a 2-frame traceback.
# Also embeds a private token to verify redaction.
_UNITTEST_OUTPUT = """\
======================================================================
FAIL: test_x (mymodule.MyTests)
----------------------------------------------------------------------
Traceback (most recent call last):
  File "tests/test_mymodule.py", line 10, in test_x
    self.assertEqual(foo(), "expected")
  File "mymodule.py", line 5, in foo
    raise AssertionError("<private>tok</private>")
AssertionError: <private>tok</private>

----------------------------------------------------------------------
Ran 3 tests in 0.005s

FAILED (failures=1)
"""

_OBSERVED_AT = datetime(2026, 6, 2, 0, 0, 0, tzinfo=UTC)

# ---------------------------------------------------------------------------
# parse_pytest_output
# ---------------------------------------------------------------------------


class TestParsePytestOutput:
    def test_one_failure_returned(self) -> None:
        failures = parse_pytest_output(_PYTEST_OUTPUT)
        assert len(failures) == 1

    def test_test_id_correct(self) -> None:
        failures = parse_pytest_output(_PYTEST_OUTPUT)
        assert failures[0].test_id == "TestSuite.test_process"

    def test_traceback_parsed(self) -> None:
        failures = parse_pytest_output(_PYTEST_OUTPUT)
        assert failures[0].traceback is not None

    def test_traceback_exc_type(self) -> None:
        failures = parse_pytest_output(_PYTEST_OUTPUT)
        tb = failures[0].traceback
        assert tb is not None
        assert tb.exc_type == "RuntimeError"

    def test_traceback_frame_count(self) -> None:
        failures = parse_pytest_output(_PYTEST_OUTPUT)
        tb = failures[0].traceback
        assert tb is not None
        assert len(tb.frames) == 2

    def test_redaction_removes_private_token_from_message(self) -> None:
        failures = parse_pytest_output(_PYTEST_OUTPUT)
        tb = failures[0].traceback
        assert tb is not None
        assert "tok" not in tb.exc_message

    def test_redaction_replacement_present(self) -> None:
        failures = parse_pytest_output(_PYTEST_OUTPUT)
        tb = failures[0].traceback
        assert tb is not None
        assert "[REDACTED]" in tb.exc_message

    def test_empty_input_returns_empty_list(self) -> None:
        assert parse_pytest_output("") == []

    def test_garbage_returns_empty_list(self) -> None:
        assert parse_pytest_output("not a test output\n\nrandom text") == []

    def test_deterministic(self) -> None:
        a = parse_pytest_output(_PYTEST_OUTPUT)
        b = parse_pytest_output(_PYTEST_OUTPUT)
        assert a == b


# ---------------------------------------------------------------------------
# parse_unittest_output
# ---------------------------------------------------------------------------


class TestParseUnittestOutput:
    def test_one_failure_returned(self) -> None:
        failures = parse_unittest_output(_UNITTEST_OUTPUT)
        assert len(failures) == 1

    def test_test_id_correct(self) -> None:
        failures = parse_unittest_output(_UNITTEST_OUTPUT)
        assert failures[0].test_id == "test_x (mymodule.MyTests)"

    def test_traceback_parsed(self) -> None:
        failures = parse_unittest_output(_UNITTEST_OUTPUT)
        assert failures[0].traceback is not None

    def test_traceback_exc_type(self) -> None:
        failures = parse_unittest_output(_UNITTEST_OUTPUT)
        tb = failures[0].traceback
        assert tb is not None
        assert tb.exc_type == "AssertionError"

    def test_redaction_removes_private_token(self) -> None:
        failures = parse_unittest_output(_UNITTEST_OUTPUT)
        tb = failures[0].traceback
        assert tb is not None
        assert "tok" not in tb.exc_message
        assert "[REDACTED]" in tb.exc_message

    def test_empty_input_returns_empty_list(self) -> None:
        assert parse_unittest_output("") == []

    def test_garbage_returns_empty_list(self) -> None:
        assert parse_unittest_output("nothing useful here") == []

    def test_deterministic(self) -> None:
        a = parse_unittest_output(_UNITTEST_OUTPUT)
        b = parse_unittest_output(_UNITTEST_OUTPUT)
        assert a == b


# ---------------------------------------------------------------------------
# failures_from_test_output
# ---------------------------------------------------------------------------


class TestFailuresFromTestOutput:
    def test_pytest_explicit_runner_yields_one_failure(self) -> None:
        fms = failures_from_test_output(_PYTEST_OUTPUT, observed_at=_OBSERVED_AT, runner="pytest")
        assert len(fms) == 1

    def test_unittest_explicit_runner_yields_one_failure(self) -> None:
        fms = failures_from_test_output(
            _UNITTEST_OUTPUT, observed_at=_OBSERVED_AT, runner="unittest"
        )
        assert len(fms) == 1

    def test_auto_detects_pytest(self) -> None:
        fms = failures_from_test_output(_PYTEST_OUTPUT, observed_at=_OBSERVED_AT)
        assert len(fms) == 1

    def test_auto_detects_unittest(self) -> None:
        fms = failures_from_test_output(_UNITTEST_OUTPUT, observed_at=_OBSERVED_AT)
        assert len(fms) == 1

    def test_source_label_equals_test_id_pytest(self) -> None:
        fms = failures_from_test_output(_PYTEST_OUTPUT, observed_at=_OBSERVED_AT)
        assert fms[0].source_label == "TestSuite.test_process"

    def test_source_label_equals_test_id_unittest(self) -> None:
        fms = failures_from_test_output(_UNITTEST_OUTPUT, observed_at=_OBSERVED_AT)
        assert fms[0].source_label == "test_x (mymodule.MyTests)"

    def test_redaction_absent_from_failure_memory_pytest(self) -> None:
        fms = failures_from_test_output(_PYTEST_OUTPUT, observed_at=_OBSERVED_AT)
        fm = fms[0]
        assert "tok" not in fm.exc_message
        assert "tok" not in fm.content_hash

    def test_redaction_absent_from_failure_memory_unittest(self) -> None:
        fms = failures_from_test_output(_UNITTEST_OUTPUT, observed_at=_OBSERVED_AT)
        fm = fms[0]
        assert "tok" not in fm.exc_message

    def test_observed_at_stored_on_failure_memory(self) -> None:
        fms = failures_from_test_output(_PYTEST_OUTPUT, observed_at=_OBSERVED_AT)
        assert fms[0].observed_at == _OBSERVED_AT

    def test_provenance_defaults(self) -> None:
        fms = failures_from_test_output(_PYTEST_OUTPUT, observed_at=_OBSERVED_AT)
        assert fms[0].trust == "user"
        assert fms[0].confidence == "EXTRACTED"

    def test_content_hash_is_64_hex_chars(self) -> None:
        fms = failures_from_test_output(_PYTEST_OUTPUT, observed_at=_OBSERVED_AT)
        assert len(fms[0].content_hash) == 64

    def test_garbage_input_returns_empty_list(self) -> None:
        result = failures_from_test_output("garbage text", observed_at=_OBSERVED_AT)
        assert result == []

    def test_empty_input_returns_empty_list(self) -> None:
        result = failures_from_test_output("", observed_at=_OBSERVED_AT)
        assert result == []

    def test_deterministic_pytest(self) -> None:
        a = failures_from_test_output(_PYTEST_OUTPUT, observed_at=_OBSERVED_AT)
        b = failures_from_test_output(_PYTEST_OUTPUT, observed_at=_OBSERVED_AT)
        assert len(a) == len(b) == 1
        assert a[0].failure_id == b[0].failure_id
        assert a[0].content_hash == b[0].content_hash

    def test_deterministic_unittest(self) -> None:
        a = failures_from_test_output(_UNITTEST_OUTPUT, observed_at=_OBSERVED_AT)
        b = failures_from_test_output(_UNITTEST_OUTPUT, observed_at=_OBSERVED_AT)
        assert len(a) == len(b) == 1
        assert a[0].failure_id == b[0].failure_id

    def test_failure_without_traceback_skipped(self) -> None:
        # A pytest summary-only line (no FAILURES block) yields TestFailures
        # with traceback=None; those must be skipped.
        summary_only = (
            "=========================== short test summary info ============================\n"
            "FAILED tests/test_x.py::m - some error\n"
            "========================= 1 failed in 0.01s =================================\n"
        )
        fms = failures_from_test_output(summary_only, observed_at=_OBSERVED_AT)
        # No parseable traceback → empty list.
        assert fms == []

    def test_multiple_pytest_failures(self) -> None:
        multi = """\
=================================== FAILURES ===================================
_______ test_alpha _______

Traceback (most recent call last):
  File "tests/test_a.py", line 5, in test_alpha
    raise ValueError("bad")
ValueError: bad

_______ test_beta _______

Traceback (most recent call last):
  File "tests/test_b.py", line 8, in test_beta
    raise KeyError("missing")
KeyError: missing

"""
        fms = failures_from_test_output(multi, observed_at=_OBSERVED_AT, runner="pytest")
        assert len(fms) == 2
        labels = {fm.source_label for fm in fms}
        assert "test_alpha" in labels
        assert "test_beta" in labels

    def test_test_id_redacted_in_source_label(self) -> None:
        """B5: a secret-bearing parametrized test name is redacted before it
        becomes FailureMemory.source_label."""
        output = (
            "=== FAILURES ===\n"
            "_______ test_x[<private>tok</private>] _______\n\n"
            "Traceback (most recent call last):\n"
            '  File "/a.py", line 1, in f\n'
            "    g()\n"
            "ValueError: boom\n"
        )
        fms = failures_from_test_output(output, observed_at=_OBSERVED_AT, runner="pytest")
        assert len(fms) == 1
        assert "tok" not in fms[0].source_label
