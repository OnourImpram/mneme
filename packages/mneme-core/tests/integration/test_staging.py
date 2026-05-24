"""Integration tests for the staging capture layer."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from mneme_core.compression.staging import (
    DEFAULT_CAPTURE_TOOLS,
    DEFAULT_SIZE_CAP_BYTES,
    StagingConfig,
    capture_event,
    compute_content_hash,
    enforce_size_cap,
    redact_private,
)


@pytest.fixture
def config(tmp_path: Path) -> StagingConfig:
    return StagingConfig(
        staging_dir=tmp_path / "staging",
        audit_dir=tmp_path / "audit",
        host="testhost",
    )


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
        assert "[PRIVATE]" in result["content"]
        assert "secret" not in result["content"]

    def test_writes_audit_record(self, config: StagingConfig) -> None:
        event = {"content": "<private>s</private>", "name": "fine"}
        redact_private(event, config)
        audit_files = list(config.audit_dir.glob("*.jsonl"))
        assert len(audit_files) == 1
        rec = json.loads(audit_files[0].read_text(encoding="utf-8").strip())
        assert rec["field"] == "content"
        assert rec["host"] == "testhost"
        assert rec["original_length"] == len("<private>s</private>")
        assert len(rec["audit_hash"]) == 16

    def test_multiple_redactions_in_one_field(self, config: StagingConfig) -> None:
        event = {"x": "a<private>1</private>b<private>2</private>c"}
        redact_private(event, config)
        audit_files = list(config.audit_dir.glob("*.jsonl"))
        lines = audit_files[0].read_text(encoding="utf-8").strip().split("\n")
        assert len(lines) == 2

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
        assert "[PRIVATE]" in line


class TestEnforceSizeCap:
    def test_no_op_when_under_cap(self, config: StagingConfig) -> None:
        capture_event({"tool_name": "Write", "x": "small"}, config)
        archived = enforce_size_cap(config)
        assert archived == 0

    def test_archives_oldest_when_over_cap(
        self, config: StagingConfig, tmp_path: Path
    ) -> None:
        # Reduce cap so we can trigger archival cheaply.
        config.size_cap_bytes = 200
        # Write two events at different mtimes.
        capture_event({"tool_name": "Write", "data": "x" * 150}, config)
        import time

        time.sleep(0.05)
        capture_event({"tool_name": "Write", "data": "y" * 150}, config)
        archived = enforce_size_cap(config)
        assert archived >= 1
        assert (config.staging_dir / "archive").exists()

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
