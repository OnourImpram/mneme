"""Integration tests for the staging capture layer."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from mneme_core.compression.staging import (
    BULKY_RESPONSE_FIELD,
    DEFAULT_CAPTURE_TOOLS,
    DEFAULT_SIZE_CAP_BYTES,
    OMITTED_RESPONSE_FIELD,
    StagingConfig,
    capture_event,
    compute_content_hash,
    enforce_size_cap,
    redact_private,
    summarize_bulky_response_fields,
)


@pytest.fixture
def config(tmp_path: Path) -> StagingConfig:
    return StagingConfig(
        staging_dir=tmp_path / "staging",
        audit_dir=tmp_path / "audit",
        host="testhost",
    )


def _edit_event(original: str = "line one\nline two\n") -> dict[str, object]:
    """An Edit event shaped like the real Claude Code PostToolUse payload."""
    return {
        "tool_name": "Edit",
        "tool_input": {"file_path": "notes.md"},
        "tool_response": {
            "filePath": "notes.md",
            "oldString": "line one",
            "newString": "line 1",
            BULKY_RESPONSE_FIELD: original,
            "userModified": False,
        },
    }


class TestBulkyResponseElision:
    """``tool_response.originalFile`` is the whole pre-edit file. Nothing
    reads it, so it is replaced by a digest — without breaking capture."""

    def test_content_is_replaced_by_digest_and_size(self) -> None:
        event = _edit_event("alpha\nbeta\n")
        assert summarize_bulky_response_fields(event) is True
        resp = event["tool_response"]
        assert isinstance(resp, dict)
        assert BULKY_RESPONSE_FIELD not in resp
        omitted = resp[OMITTED_RESPONSE_FIELD]
        assert len(omitted["sha256_16"]) == 16
        assert omitted["bytes"] == len(b"alpha\nbeta\n")

    def test_digest_is_stable_and_discriminating(self) -> None:
        a, b = _edit_event("same"), _edit_event("same")
        c = _edit_event("different")
        for e in (a, b, c):
            summarize_bulky_response_fields(e)

        def digest(e: dict[str, object]) -> str:
            resp = e["tool_response"]
            assert isinstance(resp, dict)
            return str(resp[OMITTED_RESPONSE_FIELD]["sha256_16"])

        assert digest(a) == digest(b)
        assert digest(a) != digest(c)

    def test_no_op_when_field_absent(self) -> None:
        event = {"tool_name": "Bash", "tool_response": {"stdout": "ok"}}
        assert summarize_bulky_response_fields(event) is False
        assert event["tool_response"] == {"stdout": "ok"}

    def test_no_op_when_response_is_not_a_dict(self) -> None:
        event = {"tool_name": "Edit", "tool_response": "plain string"}
        assert summarize_bulky_response_fields(event) is False

    def test_non_string_value_is_left_alone(self) -> None:
        event = {"tool_name": "Edit", "tool_response": {BULKY_RESPONSE_FIELD: 42}}
        assert summarize_bulky_response_fields(event) is False
        resp = event["tool_response"]
        assert isinstance(resp, dict)
        assert resp[BULKY_RESPONSE_FIELD] == 42

    def test_capture_still_stages_the_event(self, config: StagingConfig) -> None:
        """The elision must not cost us the event: same record, minus ballast."""
        body = "x" * 5000
        assert capture_event(_edit_event(body), config) is True
        files = list(config.staging_dir.rglob("*-events.jsonl"))
        assert len(files) == 1
        rec = json.loads(files[0].read_text(encoding="utf-8").strip())
        # Still a complete, identifiable record.
        assert rec["tool_name"] == "Edit"
        assert rec["tool_response"]["filePath"] == "notes.md"
        assert rec["tool_response"]["oldString"] == "line one"
        assert rec["content_hash"] and rec["captured_at"]
        # Ballast gone, digest present.
        assert BULKY_RESPONSE_FIELD not in rec["tool_response"]
        assert rec["tool_response"][OMITTED_RESPONSE_FIELD]["bytes"] == 5000

    def test_file_body_does_not_reach_disk(self, config: StagingConfig) -> None:
        needle = "SENTINEL-BODY-NOT-STAGED"
        assert capture_event(_edit_event(f"a\n{needle}\nb\n"), config) is True
        files = list(config.staging_dir.rglob("*-events.jsonl"))
        raw = files[0].read_text(encoding="utf-8")
        assert needle not in raw
        # Positive control: the same channel does carry other response text,
        # so the absence above is elision and not a dead assertion.
        assert "line one" in raw

    def test_staged_bytes_drop_substantially(self, config: StagingConfig) -> None:
        big = "y" * 40_000
        assert capture_event(_edit_event(big), config) is True
        staged = sum(
            f.stat().st_size for f in config.staging_dir.rglob("*-events.jsonl")
        )
        assert staged < 2_000


class TestCaptureEvent:
    def test_writes_eligible_event(self, config: StagingConfig) -> None:
        ok = capture_event({"tool_name": "Edit", "args": {"file": "x.py"}}, config)
        assert ok is True
        files = list(config.staging_dir.rglob("*-events.jsonl"))
        assert len(files) == 1

    def test_ignores_non_capture_tool(self, config: StagingConfig) -> None:
        ok = capture_event({"tool_name": "Read", "args": {}}, config)
        assert ok is False
        assert not list(config.staging_dir.rglob("*.jsonl"))

    def test_accepts_legacy_tool_key(self, config: StagingConfig) -> None:
        ok = capture_event({"tool": "Bash", "command": "ls"}, config)
        assert ok is True

    def test_ignores_empty_event(self, config: StagingConfig) -> None:
        assert capture_event({}, config) is False

    def test_does_not_raise_on_malformed_input(self, config: StagingConfig) -> None:
        # Non-dict should return False, not raise.
        assert capture_event("not-a-dict", config) is False  # type: ignore[arg-type]
        assert capture_event(None, config) is False  # type: ignore[arg-type]

    def test_writes_into_host_dated_path(self, config: StagingConfig) -> None:
        capture_event({"tool_name": "Write", "path": "foo.md"}, config)
        files = list(config.staging_dir.rglob("*-events.jsonl"))
        assert "testhost" in files[0].parts
        # Path contains a YYYY-MM-DD dated directory.
        date_dir = files[0].parent.name
        assert len(date_dir) == 10
        assert date_dir[4] == "-" and date_dir[7] == "-"

    def test_adds_captured_at_host_and_hash(self, config: StagingConfig) -> None:
        capture_event({"tool_name": "Edit", "x": 1}, config)
        files = list(config.staging_dir.rglob("*-events.jsonl"))
        line = files[0].read_text(encoding="utf-8").strip()
        rec = json.loads(line)
        assert rec["captured_at"]
        assert rec["host"] == "testhost"
        assert len(rec["content_hash"]) == 16


class TestRedactPrivate:
    def test_replaces_private_block(self, config: StagingConfig) -> None:
        event = {"content": "before <private>secret</private> after"}
        result = redact_private(event, config)
        assert "<private>" not in result["content"]
        assert "[REDACTED]" in result["content"]
        assert "secret" not in result["content"]

    def test_writes_audit_record(self, config: StagingConfig) -> None:
        event = {"content": "<private>s</private>", "name": "fine"}
        redact_private(event, config)
        audit_files = list(config.audit_dir.glob("*.jsonl"))
        assert len(audit_files) == 1
        rec = json.loads(audit_files[0].read_text(encoding="utf-8").strip())
        assert rec["field"] == "content"
        assert rec["host"] == "testhost"
        # original_length now covers the whole field value (not just the span).
        assert rec["original_length"] == len("<private>s</private>")
        assert len(rec["audit_hash"]) == 16

    def test_multiple_redactions_in_one_field(self, config: StagingConfig) -> None:
        event = {"x": "a<private>1</private>b<private>2</private>c"}
        redact_private(event, config)
        audit_files = list(config.audit_dir.glob("*.jsonl"))
        # One audit record per field that contained any private content
        # (not per match span) — the record covers the whole field.
        lines = audit_files[0].read_text(encoding="utf-8").strip().split("\n")
        assert len(lines) == 1

    def test_non_string_values_untouched(self, config: StagingConfig) -> None:
        event = {"count": 42, "flag": True, "tags": ["a", "b"]}
        result = redact_private(event, config)
        assert result["count"] == 42
        assert result["flag"] is True
        assert result["tags"] == ["a", "b"]

    def test_no_audit_when_no_match(self, config: StagingConfig) -> None:
        event = {"x": "no secrets here"}
        redact_private(event, config)
        assert not config.audit_dir.exists() or not list(
            config.audit_dir.glob("*.jsonl")
        )

    def test_capture_event_redacts_inline(self, config: StagingConfig) -> None:
        capture_event(
            {"tool_name": "Bash", "command": "echo <private>token</private>"},
            config,
        )
        line = next(config.staging_dir.rglob("*-events.jsonl")).read_text(
            encoding="utf-8"
        )
        assert "token" not in line
        assert "[REDACTED]" in line


class TestEnforceSizeCap:
    def test_no_op_when_under_cap(self, config: StagingConfig) -> None:
        capture_event({"tool_name": "Write", "x": "small"}, config)
        archived = enforce_size_cap(config)
        assert archived == 0

    def test_archives_oldest_when_over_cap(
        self, config: StagingConfig, tmp_path: Path
    ) -> None:
        # enforce_size_cap archives the oldest staging files until usage falls
        # below 90% of the cap. Write the files directly rather than via
        # capture_event: with accurate byte accounting capture_event now
        # enforces the cap eagerly on write, so driving archival through it
        # would leave nothing for the explicit call to do. Writing files lets
        # us test enforce_size_cap's archival contract in isolation.
        import os
        import time

        config.size_cap_bytes = 200
        host_dir = config.staging_dir / config.host / "2026-01-01"
        host_dir.mkdir(parents=True)
        oldest = host_dir / "00-00-events.jsonl"
        oldest.write_text("x" * 150, encoding="utf-8")
        newest = host_dir / "00-05-events.jsonl"
        newest.write_text("y" * 150, encoding="utf-8")
        # Make ``oldest`` strictly older so it is archived first.
        past = time.time() - 100
        os.utime(oldest, (past, past))

        archived = enforce_size_cap(config)
        assert archived >= 1
        assert (config.staging_dir / "archive").exists()
        # The oldest file must be the one moved into archive/.
        assert not oldest.exists()
        archived_oldest = (
            config.staging_dir
            / "archive"
            / config.host
            / "2026-01-01"
            / "00-00-events.jsonl"
        )
        assert archived_oldest.is_file()

    def test_handles_missing_directory(self, tmp_path: Path) -> None:
        cfg = StagingConfig(
            staging_dir=tmp_path / "nope",
            audit_dir=tmp_path / "audit",
        )
        assert enforce_size_cap(cfg) == 0


class TestComputeContentHash:
    def test_deterministic(self) -> None:
        a = compute_content_hash({"x": 1, "y": "z"})
        b = compute_content_hash({"y": "z", "x": 1})
        assert a == b

    def test_distinguishes_distinct_payloads(self) -> None:
        a = compute_content_hash({"x": 1})
        b = compute_content_hash({"x": 2})
        assert a != b

    def test_hex_length(self) -> None:
        h = compute_content_hash({"any": "value"})
        assert len(h) == 16
        assert all(c in "0123456789abcdef" for c in h)


class TestDefaults:
    def test_default_capture_tools(self) -> None:
        assert "Edit" in DEFAULT_CAPTURE_TOOLS
        assert "Bash" in DEFAULT_CAPTURE_TOOLS
        assert "MultiEdit" in DEFAULT_CAPTURE_TOOLS
        assert "Read" not in DEFAULT_CAPTURE_TOOLS

    def test_default_cap_is_100mb(self) -> None:
        assert DEFAULT_SIZE_CAP_BYTES == 100 * 1024 * 1024


class TestContentHashDedup:
    """content_hash must be stable across time and host; distinct for distinct content."""

    def test_same_content_different_captured_at_same_hash(self) -> None:
        # Identical events captured at different times must hash identically.
        base = {"tool_name": "Edit", "path": "foo.py", "content": "x = 1"}
        h1 = compute_content_hash({**base, "captured_at": "2026-01-01T00:00:00+00:00"})
        h2 = compute_content_hash({**base, "captured_at": "2026-06-15T12:34:56+00:00"})
        assert h1 == h2

    def test_same_content_different_host_same_hash(self) -> None:
        base = {"tool_name": "Write", "path": "bar.md"}
        h1 = compute_content_hash({**base, "host": "machine-a"})
        h2 = compute_content_hash({**base, "host": "machine-b"})
        assert h1 == h2

    def test_same_content_all_ephemeral_fields_same_hash(self) -> None:
        base = {"tool_name": "Bash", "command": "ls"}
        h1 = compute_content_hash(
            {
                **base,
                "captured_at": "2026-01-01T00:00:00+00:00",
                "host": "a",
                "content_hash": "old",
            }
        )
        h2 = compute_content_hash(
            {
                **base,
                "captured_at": "2099-12-31T23:59:59+00:00",
                "host": "z",
                "content_hash": "stale",
            }
        )
        assert h1 == h2

    def test_different_content_different_hash(self) -> None:
        h1 = compute_content_hash({"tool_name": "Edit", "path": "a.py"})
        h2 = compute_content_hash({"tool_name": "Edit", "path": "b.py"})
        assert h1 != h2

    def test_write_event_hash_stable_across_captures(
        self, config: StagingConfig, tmp_path: Path
    ) -> None:
        # Two capture_event calls with the same logical content (different times
        # because _now() advances) must write the same content_hash to disk.
        event_a = {"tool_name": "Edit", "path": "stable.py", "x": "val"}
        event_b = {"tool_name": "Edit", "path": "stable.py", "x": "val"}
        capture_event(event_a, config)
        capture_event(event_b, config)
        records = []
        for f in sorted(config.staging_dir.rglob("*-events.jsonl")):
            for line in f.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    records.append(json.loads(line))
        assert len(records) == 2
        assert records[0]["content_hash"] == records[1]["content_hash"]
