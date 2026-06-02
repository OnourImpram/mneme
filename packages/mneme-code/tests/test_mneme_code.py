"""Tests for mneme-code: traceback parsing, failure memories, frame resolution, CLI."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

import pytest

from mneme_code.failure import failure_from_traceback, failure_to_markdown
from mneme_code.resolve import resolve_frames
from mneme_code.stacktrace import parse_traceback

# A realistic two-frame CPython traceback. The exception message and the
# innermost code line both carry a private token to exercise redaction.
TRACEBACK = (
    "Traceback (most recent call last):\n"
    '  File "/abs/proj/src/app.py", line 10, in main\n'
    "    do_thing(payload)\n"
    '  File "/abs/proj/src/worker.py", line 4, in do_thing\n'
    '    raise ValueError("<private>topsecret</private>")\n'
    "ValueError: <private>topsecret</private>\n"
)


# ---------------------------------------------------------------------------
# stacktrace
# ---------------------------------------------------------------------------


class TestParseTraceback:
    def test_parses_exc_type_and_frame_count(self) -> None:
        parsed = parse_traceback(TRACEBACK)
        assert parsed is not None
        assert parsed.exc_type == "ValueError"
        assert len(parsed.frames) == 2

    def test_first_and_last_frame_coordinates(self) -> None:
        parsed = parse_traceback(TRACEBACK)
        assert parsed is not None
        first, last = parsed.frames[0], parsed.frames[-1]
        assert (first.file_path, first.line, first.function) == (
            "/abs/proj/src/app.py",
            10,
            "main",
        )
        assert (last.file_path, last.line, last.function) == (
            "/abs/proj/src/worker.py",
            4,
            "do_thing",
        )

    def test_redaction_removes_secret_from_message(self) -> None:
        parsed = parse_traceback(TRACEBACK)
        assert parsed is not None
        assert "topsecret" not in parsed.exc_message

    def test_redaction_removes_secret_from_code_context(self) -> None:
        parsed = parse_traceback(TRACEBACK)
        assert parsed is not None
        joined = " ".join(fr.code_context or "" for fr in parsed.frames)
        assert "topsecret" not in joined

    def test_empty_input_returns_none(self) -> None:
        assert parse_traceback("") is None

    def test_non_traceback_returns_none(self) -> None:
        assert parse_traceback("just some log line\nnothing here") is None

    def test_dotted_exception_type(self) -> None:
        text = (
            "Traceback (most recent call last):\n"
            '  File "/a.py", line 1, in f\n'
            "    g()\n"
            "pkg.mod.CustomError: boom\n"
        )
        parsed = parse_traceback(text)
        assert parsed is not None
        assert parsed.exc_type == "pkg.mod.CustomError"
        assert parsed.exc_message == "boom"

    def test_exception_without_message(self) -> None:
        text = (
            "Traceback (most recent call last):\n"
            '  File "/a.py", line 1, in f\n'
            "    raise StopIteration\n"
            "StopIteration\n"
        )
        parsed = parse_traceback(text)
        assert parsed is not None
        assert parsed.exc_type == "StopIteration"
        assert parsed.exc_message == ""

    def test_never_raises_on_garbage(self) -> None:
        # Pathological input must yield None, not an exception.
        assert parse_traceback("Traceback (most recent call last):\n") is None

    def test_truncated_traceback_returns_none(self) -> None:
        # A traceback truncated before the exception line ends in an indented
        # code-context line (here a bare keyword). It must NOT be misread as the
        # exception type — review-debt finding #1.
        text = (
            "Traceback (most recent call last):\n"
            '  File "/a.py", line 1, in f\n'
            "    pass\n"
        )
        assert parse_traceback(text) is None


# ---------------------------------------------------------------------------
# failure
# ---------------------------------------------------------------------------


class TestFailureMemory:
    def test_deterministic_id_and_hash(self) -> None:
        parsed = parse_traceback(TRACEBACK)
        assert parsed is not None
        at = datetime(2026, 6, 2, tzinfo=UTC)
        a = failure_from_traceback(parsed, observed_at=at, source_label="run-1")
        b = failure_from_traceback(parsed, observed_at=at, source_label="run-1")
        assert a.failure_id == b.failure_id
        assert a.content_hash == b.content_hash

    def test_provenance_and_confidence(self) -> None:
        parsed = parse_traceback(TRACEBACK)
        assert parsed is not None
        f = failure_from_traceback(parsed, observed_at=datetime(2026, 6, 2, tzinfo=UTC))
        assert f.trust == "user"
        assert f.confidence == "EXTRACTED"
        assert len(f.content_hash) == 64

    def test_markdown_frontmatter_type_and_utc_created(self) -> None:
        parsed = parse_traceback(TRACEBACK)
        assert parsed is not None
        # observed_at in +03:00 == 2026-06-02T00:00:00Z; created must normalise to UTC.
        at = datetime(2026, 6, 2, 3, 0, 0, tzinfo=timezone(timedelta(hours=3)))
        md = failure_to_markdown(failure_from_traceback(parsed, observed_at=at))
        assert "type: failure" in md
        assert "created: 2026-06-02T00:00:00.000000+00:00" in md

    def test_markdown_source_label_is_yaml_safe(self) -> None:
        parsed = parse_traceback(TRACEBACK)
        assert parsed is not None
        f = failure_from_traceback(
            parsed, observed_at=datetime(2026, 6, 2, tzinfo=UTC), source_label="test: foo#bar"
        )
        md = failure_to_markdown(f)
        # JSON-encoded -> quoted, so the colon/hash cannot break the YAML frontmatter.
        assert 'source_label: "test: foo#bar"' in md

    def test_markdown_does_not_leak_secret(self) -> None:
        parsed = parse_traceback(TRACEBACK)
        assert parsed is not None
        md = failure_to_markdown(
            failure_from_traceback(parsed, observed_at=datetime(2026, 6, 2, tzinfo=UTC))
        )
        assert "topsecret" not in md


# ---------------------------------------------------------------------------
# resolve
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _Node:
    name: str
    source_path: str
    kind: str


class _Store:
    def __init__(self, nodes: list[_Node]) -> None:
        self._nodes = nodes

    @property
    def nodes(self) -> list[_Node]:
        return self._nodes


class TestResolveFrames:
    def test_matches_absolute_frame_to_relative_node(self) -> None:
        parsed = parse_traceback(TRACEBACK)
        assert parsed is not None
        # Node source_path is vault-relative; frame file_path is absolute.
        store = _Store(
            [
                _Node(name="main", source_path="src/app.py", kind="function"),
                _Node(name="do_thing", source_path="src/worker.py", kind="function"),
            ]
        )
        pairs = resolve_frames(parsed, store)
        resolved = {fr.function: nd for fr, nd in pairs}
        assert resolved["main"] is not None
        assert resolved["do_thing"] is not None

    def test_unmatched_frame_is_none(self) -> None:
        parsed = parse_traceback(TRACEBACK)
        assert parsed is not None
        store = _Store([_Node(name="main", source_path="src/app.py", kind="function")])
        pairs = {fr.function: nd for fr, nd in resolve_frames(parsed, store)}
        assert pairs["do_thing"] is None  # no node for it

    def test_non_function_node_is_not_matched(self) -> None:
        parsed = parse_traceback(TRACEBACK)
        assert parsed is not None
        store = _Store([_Node(name="main", source_path="src/app.py", kind="class")])
        pairs = {fr.function: nd for fr, nd in resolve_frames(parsed, store)}
        assert pairs["main"] is None  # kind != function

    def test_none_store_returns_all_none(self) -> None:
        parsed = parse_traceback(TRACEBACK)
        assert parsed is not None
        assert all(nd is None for _, nd in resolve_frames(parsed, None))

    def test_empty_store_returns_all_none(self) -> None:
        parsed = parse_traceback(TRACEBACK)
        assert parsed is not None
        assert all(nd is None for _, nd in resolve_frames(parsed, _Store([])))


# ---------------------------------------------------------------------------
# cli
# ---------------------------------------------------------------------------


def _run_main(argv: list[str], monkeypatch: pytest.MonkeyPatch) -> int:
    from mneme_code import cli

    monkeypatch.setattr("sys.argv", argv)
    with pytest.raises(SystemExit) as exc:
        cli.main()
    code = exc.value.code
    return code if isinstance(code, int) else 1


class TestCli:
    def test_happy_path_exits_zero(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        f = tmp_path / "trace.txt"
        f.write_text(TRACEBACK, encoding="utf-8")
        assert _run_main(["mneme-code", "parse-trace", "--input", str(f)], monkeypatch) == 0

    def test_not_a_traceback_exits_one(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        f = tmp_path / "junk.txt"
        f.write_text("not a traceback", encoding="utf-8")
        assert _run_main(["mneme-code", "parse-trace", "--input", str(f)], monkeypatch) == 1

    def test_write_creates_redacted_note(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        f = tmp_path / "trace.txt"
        f.write_text(TRACEBACK, encoding="utf-8")
        rc = _run_main(
            [
                "mneme-code", "parse-trace", "--input", str(f),
                "--write", "--vault", str(tmp_path), "--source", "ci-1",
            ],
            monkeypatch,
        )
        assert rc == 0
        notes = list((tmp_path / "code-failures").glob("*.md"))
        assert len(notes) == 1
        content = notes[0].read_text(encoding="utf-8")
        assert "type: failure" in content
        assert "topsecret" not in content


def test_public_api_exports() -> None:
    import mneme_code

    for name in (
        "Frame",
        "ParsedTraceback",
        "parse_traceback",
        "FailureMemory",
        "failure_from_traceback",
        "failure_to_markdown",
        "resolve_frames",
    ):
        assert hasattr(mneme_code, name)
