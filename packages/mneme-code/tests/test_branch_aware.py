"""Branch-aware failure notes: frontmatter metadata, determinism preserved."""

from __future__ import annotations

from datetime import UTC, datetime

from mneme_code.failure import failure_from_traceback, failure_to_markdown
from mneme_code.stacktrace import parse_traceback

_TRACE = """Traceback (most recent call last):
  File "src/app.py", line 10, in run
    value = compute()
  File "src/calc.py", line 4, in compute
    return 1 / 0
ZeroDivisionError: division by zero
"""

_OBSERVED = datetime(2026, 6, 1, 12, 0, 0, tzinfo=UTC)


def _failure():
    parsed = parse_traceback(_TRACE)
    assert parsed is not None
    return failure_from_traceback(parsed, observed_at=_OBSERVED)


class TestBranchAwareMarkdown:
    def test_branch_recorded_in_frontmatter(self) -> None:
        note = failure_to_markdown(_failure(), branch="feat/3.0-next-level")
        assert 'branch: "feat/3.0-next-level"' in note

    def test_no_branch_omits_field(self) -> None:
        note = failure_to_markdown(_failure())
        assert "branch:" not in note

    def test_branch_does_not_change_identity(self) -> None:
        a = _failure()
        b = _failure()
        assert a.failure_id == b.failure_id
        assert a.content_hash == b.content_hash
        with_branch = failure_to_markdown(a, branch="main")
        without = failure_to_markdown(b)
        # Identity lines are identical; only the branch line differs.
        assert f"id: {a.failure_id}" in with_branch
        assert f"id: {b.failure_id}" in without

    def test_private_span_in_branch_name_redacted(self) -> None:
        note = failure_to_markdown(
            _failure(), branch="exp/<private>secret-client</private>-fix"
        )
        assert "secret-client" not in note
        assert "[REDACTED]" in note
