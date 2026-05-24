"""Integration tests for the telemetry writer."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from mneme_core.telemetry.writer import (
    COMMON_GENERIC_CREDENTIAL_PATTERNS,
    COMMON_PII_TURKISH_NATIONAL_ID,
    VALID_EVENT_CLASSES,
    TelemetryConfig,
    redact_pii,
    write_event,
)


@pytest.fixture
def config(tmp_path: Path) -> TelemetryConfig:
    return TelemetryConfig(
        telemetry_dir=tmp_path / "telemetry",
        audit_dir=tmp_path / "audit",
        host="testhost",
    )


@pytest.fixture
def config_with_credentials(tmp_path: Path) -> TelemetryConfig:
    return TelemetryConfig(
        telemetry_dir=tmp_path / "telemetry",
        audit_dir=tmp_path / "audit",
        host="testhost",
        pii_patterns=COMMON_GENERIC_CREDENTIAL_PATTERNS,
    )


class TestRedactPii:
    def test_no_patterns_returns_input_unchanged(self) -> None:
        assert redact_pii("hello world", ()) == "hello world"

    def test_replaces_when_pattern_matches(self) -> None:
        patterns = (
            (re.compile(r"\bsecret\b"), "[X]"),
        )
        result = redact_pii("the secret here", patterns)
        assert result == "the [X] here"

    def test_calls_audit_callback_with_pattern_sources(self) -> None:
        seen: list[list[str]] = []
        patterns = (
            (re.compile(r"\bfoo\b"), "[Y]"),
            (re.compile(r"\bbar\b"), "[Z]"),
        )
        redact_pii("foo and bar", patterns, audit_callback=seen.append)
        assert seen == [[r"\bfoo\b", r"\bbar\b"]]

    def test_no_audit_when_no_match(self) -> None:
        seen: list[list[str]] = []
        patterns = ((re.compile(r"\bxxx\b"), "[X]"),)
        redact_pii("nothing here", patterns, audit_callback=seen.append)
        assert seen == []

    def test_applies_multiple_patterns_in_order(self) -> None:
        patterns = (
            (re.compile(r"foo"), "BAR"),
            (re.compile(r"BAR"), "BAZ"),
        )
        # Pattern 1: foo -> BAR. Pattern 2 (applied after): BAR -> BAZ.
        assert redact_pii("foo", patterns) == "BAZ"


class TestCommonPatterns:
    def test_credential_pattern_matches_password_assignment(self) -> None:
        result = redact_pii(
            "config: password=hunter2",
            COMMON_GENERIC_CREDENTIAL_PATTERNS,
        )
        assert "hunter2" not in result
        assert "[CREDENTIAL-REDACTED]" in result

    def test_credential_pattern_matches_api_key(self) -> None:
        result = redact_pii(
            "api_key: sk-abc123",
            COMMON_GENERIC_CREDENTIAL_PATTERNS,
        )
        assert "sk-abc123" not in result

    def test_credential_pattern_case_insensitive(self) -> None:
        result = redact_pii(
            "API_KEY = XYZ",
            COMMON_GENERIC_CREDENTIAL_PATTERNS,
        )
        assert "XYZ" not in result

    def test_tr_id_pattern_matches_11_digits(self) -> None:
        result = redact_pii("ID: 12345678901", COMMON_PII_TURKISH_NATIONAL_ID)
        assert "12345678901" not in result
        assert "[TR-ID-REDACTED]" in result

    def test_tr_id_pattern_skips_leading_zero(self) -> None:
        # Turkish national IDs cannot start with zero.
        result = redact_pii("ID: 01234567890", COMMON_PII_TURKISH_NATIONAL_ID)
        assert "01234567890" in result

    def test_combining_pattern_sets(self) -> None:
        combined = COMMON_GENERIC_CREDENTIAL_PATTERNS + COMMON_PII_TURKISH_NATIONAL_ID
        result = redact_pii(
            "password=foo and id 19876543210 listed",
            combined,
        )
        assert "foo" not in result
        assert "19876543210" not in result


class TestWriteEvent:
    def test_writes_tool_use_to_dated_directory(
        self, config: TelemetryConfig
    ) -> None:
        ok = write_event("tool_use", "Edit", config)
        assert ok is True
        files = list(config.telemetry_dir.rglob("tool-usage.jsonl"))
        assert len(files) == 1

    def test_rejects_invalid_class(self, config: TelemetryConfig) -> None:
        assert write_event("not_a_class", "x", config) is False
        assert not list(config.telemetry_dir.rglob("*.jsonl"))

    def test_routes_hook_class_to_session_file(
        self, config: TelemetryConfig
    ) -> None:
        write_event("hook", "PostToolUse", config, session_id="sess-42")
        files = list(config.telemetry_dir.rglob("session-sess-42.jsonl"))
        assert len(files) == 1

    def test_writes_record_with_required_fields(
        self, config: TelemetryConfig
    ) -> None:
        write_event("tool_use", "Edit", config, session_id="s1", count=3)
        path = next(config.telemetry_dir.rglob("tool-usage.jsonl"))
        rec = json.loads(path.read_text(encoding="utf-8").strip())
        assert rec["event_class"] == "tool_use"
        assert rec["name"] == "Edit"
        assert rec["session_id"] == "s1"
        assert rec["host"] == "testhost"
        assert rec["status"] == "success"
        assert rec["count"] == 3
        assert rec["ts"]

    def test_error_status_duplicates_to_errors_log(
        self, config: TelemetryConfig
    ) -> None:
        write_event("tool_use", "Bash", config, status="error")
        primary = list(config.telemetry_dir.rglob("tool-usage.jsonl"))
        errors = list(config.telemetry_dir.rglob("errors.jsonl"))
        assert len(primary) == 1
        assert len(errors) == 1

    def test_error_class_does_not_duplicate(self, config: TelemetryConfig) -> None:
        write_event("error", "something", config, status="error")
        errors = list(config.telemetry_dir.rglob("errors.jsonl"))
        # Only one errors.jsonl, with the single record.
        assert len(errors) == 1
        lines = errors[0].read_text(encoding="utf-8").strip().split("\n")
        assert len(lines) == 1

    def test_pii_redacted_in_event_name(
        self, config_with_credentials: TelemetryConfig
    ) -> None:
        write_event(
            "tool_use",
            "ran password=hunter2",
            config_with_credentials,
        )
        path = next(config_with_credentials.telemetry_dir.rglob("tool-usage.jsonl"))
        rec = json.loads(path.read_text(encoding="utf-8").strip())
        assert "hunter2" not in rec["name"]

    def test_pii_redacted_in_string_kwargs(
        self, config_with_credentials: TelemetryConfig
    ) -> None:
        write_event(
            "tool_use",
            "ok",
            config_with_credentials,
            command="echo api_key=sk-xyz",
        )
        path = next(config_with_credentials.telemetry_dir.rglob("tool-usage.jsonl"))
        rec = json.loads(path.read_text(encoding="utf-8").strip())
        assert "sk-xyz" not in rec["command"]

    def test_pii_does_not_redact_non_strings(
        self, config_with_credentials: TelemetryConfig
    ) -> None:
        write_event(
            "tool_use",
            "ok",
            config_with_credentials,
            count=42,
            flag=True,
            tags=["a", "b"],
        )
        path = next(config_with_credentials.telemetry_dir.rglob("tool-usage.jsonl"))
        rec = json.loads(path.read_text(encoding="utf-8").strip())
        assert rec["count"] == 42
        assert rec["flag"] is True
        assert rec["tags"] == ["a", "b"]

    def test_audit_log_written_on_redaction(
        self, config_with_credentials: TelemetryConfig
    ) -> None:
        write_event(
            "tool_use",
            "password=hunter2",
            config_with_credentials,
        )
        audit_files = list(
            config_with_credentials.audit_dir.glob("*.jsonl")
        )
        assert len(audit_files) == 1
        rec = json.loads(audit_files[0].read_text(encoding="utf-8").strip())
        assert rec["host"] == "testhost"
        assert "patterns_matched" in rec
        assert rec["patterns_matched"]


class TestConstants:
    def test_valid_event_classes_set(self) -> None:
        for cls in ["tool_use", "mcp_call", "hook", "llm_call", "error", "plugin_event"]:
            assert cls in VALID_EVENT_CLASSES

    def test_credential_pattern_is_tuple(self) -> None:
        assert isinstance(COMMON_GENERIC_CREDENTIAL_PATTERNS, tuple)
        assert len(COMMON_GENERIC_CREDENTIAL_PATTERNS) >= 1

    def test_tr_id_pattern_is_tuple(self) -> None:
        assert isinstance(COMMON_PII_TURKISH_NATIONAL_ID, tuple)
        assert len(COMMON_PII_TURKISH_NATIONAL_ID) >= 1
