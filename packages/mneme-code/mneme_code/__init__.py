"""mneme-code: deterministic Python code-failure memory layer.

Design invariants
-----------------
* No LLM, no network — pure stdlib + mneme-core + mneme-graph.
* Redact-before-store — ``mneme_core.privacy.redact`` is applied to
  ``exc_message``, every ``code_context``, and every ``file_path``
  *before* any dataclass is constructed or any text is rendered.
* Provenance on every record — ``content_hash`` (sha256 of canonical
  redacted rendering), ``observed_at`` (injected by caller; never
  produced inside library functions), ``trust`` (default ``"user"``),
  and ``confidence`` (default ``"EXTRACTED"``).
* Deterministic — same inputs always produce the same ``failure_id``
  and ``content_hash``; no ``datetime.now()`` or ``random`` inside
  library functions.
* Markdown is the ground-truth artifact — ``failure_to_markdown``
  produces a vault-ready note; the vault is the store.

DEFER (not built in this version)
----------------------------------
* Live in-process test-runner plugin (a pytest hook). Console-output parsing
  of pytest/unittest IS built — see ``testrun``.
* Branch-aware failure tracking.
* Repository runbook generation.
* Non-Python tracebacks (JavaScript, Rust, Go, etc.).

Public API
----------
    from mneme_code.stacktrace import Frame, ParsedTraceback, parse_traceback
    from mneme_code.failure import FailureMemory, failure_from_traceback, failure_to_markdown
    from mneme_code.resolve import resolve_frames
    from mneme_code.agents import ProceduralMemory, parse_agents_md, procedural_to_markdown
    from mneme_code.testrun import (
        TestFailure, parse_pytest_output, parse_unittest_output, failures_from_test_output,
    )
    from mneme_code.trajectory import FixTrajectory, fix_from_failure, fix_to_markdown
"""

from mneme_code.agents import ProceduralMemory, parse_agents_md, procedural_to_markdown
from mneme_code.failure import FailureMemory, failure_from_traceback, failure_to_markdown
from mneme_code.resolve import resolve_frames
from mneme_code.stacktrace import Frame, ParsedTraceback, parse_traceback
from mneme_code.testrun import (
    TestFailure,
    failures_from_test_output,
    parse_pytest_output,
    parse_unittest_output,
)
from mneme_code.trajectory import FixTrajectory, fix_from_failure, fix_to_markdown

__version__ = "0.2.0"
__all__ = [
    "__version__",
    "Frame",
    "ParsedTraceback",
    "parse_traceback",
    "FailureMemory",
    "failure_from_traceback",
    "failure_to_markdown",
    "resolve_frames",
    "ProceduralMemory",
    "parse_agents_md",
    "procedural_to_markdown",
    "TestFailure",
    "parse_pytest_output",
    "parse_unittest_output",
    "failures_from_test_output",
    "FixTrajectory",
    "fix_from_failure",
    "fix_to_markdown",
]
