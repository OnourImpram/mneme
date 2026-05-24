"""Unit tests for the real ClaudeMemAdapter wiring.

These tests do not require an installed claude-mem binary. Subprocess
invocations are mocked, and the parser is exercised directly with
representative JSON shapes.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from adapters import AdapterHit, AdapterStatus, ClaudeMemAdapter


class TestStatusResolution:
    def test_unavailable_when_no_bin(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("CLAUDE_MEM_BIN", raising=False)
        monkeypatch.setattr(
            "shutil.which", lambda name: None if name == "claude-mem" else "/x"
        )
        adapter = ClaudeMemAdapter()
        status = adapter.status()
        assert status.available is False
        assert "claude-mem binary not found" in status.reason

    def test_available_when_env_var_set(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("CLAUDE_MEM_BIN", "/usr/local/bin/claude-mem")
        adapter = ClaudeMemAdapter()
        status = adapter.status()
        assert status.available is True
        assert status.extras["bin"] == "/usr/local/bin/claude-mem"

    def test_available_when_on_path(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("CLAUDE_MEM_BIN", raising=False)
        monkeypatch.setattr(
            "shutil.which",
            lambda name: "/opt/bin/claude-mem" if name == "claude-mem" else None,
        )
        adapter = ClaudeMemAdapter()
        status = adapter.status()
        assert status.available is True


class TestOutputParsing:
    def test_top_level_array(self) -> None:
        text = json.dumps(
            [
                {"id": "doc-1", "score": 0.95},
                {"id": "doc-2", "score": 0.80},
            ]
        )
        hits = ClaudeMemAdapter._parse_output(text)
        assert len(hits) == 2
        assert hits[0] == AdapterHit(doc_id="doc-1", score=0.95)

    def test_hits_envelope(self) -> None:
        text = json.dumps({"hits": [{"id": "doc-9", "score": 0.5}]})
        hits = ClaudeMemAdapter._parse_output(text)
        assert hits == [AdapterHit(doc_id="doc-9", score=0.5)]

    def test_results_envelope(self) -> None:
        text = json.dumps({"results": [{"doc_id": "abc", "rrf_score": 0.3}]})
        hits = ClaudeMemAdapter._parse_output(text)
        assert hits == [AdapterHit(doc_id="abc", score=0.3)]

    def test_empty_string_returns_empty_list(self) -> None:
        assert ClaudeMemAdapter._parse_output("") == []
        assert ClaudeMemAdapter._parse_output("   \n  ") == []

    def test_invalid_json_returns_empty_list(self) -> None:
        assert ClaudeMemAdapter._parse_output("not json {{{") == []

    def test_unexpected_type_returns_empty_list(self) -> None:
        # top-level is a string, not a list or dict
        assert ClaudeMemAdapter._parse_output(json.dumps("not a result")) == []

    def test_rows_without_id_skipped(self) -> None:
        text = json.dumps(
            [
                {"score": 0.5},                # no id
                {"id": "good", "score": 0.4},  # kept
            ]
        )
        hits = ClaudeMemAdapter._parse_output(text)
        assert hits == [AdapterHit(doc_id="good", score=0.4)]

    def test_non_numeric_score_defaults_to_zero(self) -> None:
        text = json.dumps([{"id": "x", "score": "not-a-number"}])
        hits = ClaudeMemAdapter._parse_output(text)
        assert hits == [AdapterHit(doc_id="x", score=0.0)]


class TestCommandBuilding:
    def test_command_shape(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("CLAUDE_MEM_BIN", "/bin/claude-mem")
        adapter = ClaudeMemAdapter()
        adapter.status()
        fixture = Path("/tmp/fixture.db")
        adapter._db_for_subprocess = fixture
        cmd = adapter._build_command("hello world", top_k=7)
        assert cmd[0] == "/bin/claude-mem"
        assert "search" in cmd
        assert "--db" in cmd
        # Path stringification varies between POSIX and Windows; check the
        # stringified path the adapter actually produced is present.
        assert str(fixture) in cmd
        assert "--json" in cmd
        assert "--top-k" in cmd
        assert "7" in cmd
        assert cmd[-1] == "hello world"
        assert cmd[-2] == "--"

    def test_extra_args_included(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("CLAUDE_MEM_BIN", "/bin/claude-mem")
        adapter = ClaudeMemAdapter(extra_search_args=["--no-color"])
        adapter.status()
        cmd = adapter._build_command("q", top_k=5)
        assert "--no-color" in cmd

    def test_build_command_raises_when_no_bin(self) -> None:
        adapter = ClaudeMemAdapter()
        with pytest.raises(RuntimeError):
            adapter._build_command("anything", top_k=5)


class TestSearchFallbacks:
    def _setup(self, monkeypatch: pytest.MonkeyPatch) -> ClaudeMemAdapter:
        monkeypatch.setenv("CLAUDE_MEM_BIN", "/bin/claude-mem")
        adapter = ClaudeMemAdapter(timeout_s=1)
        adapter.status()
        return adapter

    def test_returns_empty_on_timeout(self, monkeypatch: pytest.MonkeyPatch) -> None:
        adapter = self._setup(monkeypatch)

        def fake_run(*_args, **_kwargs):
            raise subprocess.TimeoutExpired(cmd=["claude-mem"], timeout=1)

        monkeypatch.setattr("subprocess.run", fake_run)
        assert adapter.search("anything") == []

    def test_returns_empty_on_oserror(self, monkeypatch: pytest.MonkeyPatch) -> None:
        adapter = self._setup(monkeypatch)

        def fake_run(*_args, **_kwargs):
            raise OSError("binary missing")

        monkeypatch.setattr("subprocess.run", fake_run)
        assert adapter.search("anything") == []

    def test_returns_empty_on_nonzero_exit(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        adapter = self._setup(monkeypatch)

        class FakeProc:
            returncode = 1
            stdout = ""
            stderr = "error"

        monkeypatch.setattr("subprocess.run", lambda *a, **kw: FakeProc())
        assert adapter.search("anything") == []

    def test_returns_parsed_hits_on_success(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        adapter = self._setup(monkeypatch)

        class FakeProc:
            returncode = 0
            stdout = json.dumps([{"id": "doc-a", "score": 0.9}])
            stderr = ""

        monkeypatch.setattr("subprocess.run", lambda *a, **kw: FakeProc())
        hits = adapter.search("anything")
        assert hits == [AdapterHit(doc_id="doc-a", score=0.9)]


class TestMigrate:
    def test_copies_fixture(self, tmp_path: Path) -> None:
        source = tmp_path / "source.db"
        source.write_bytes(b"fake sqlite blob")
        workdir = tmp_path / "wd"
        workdir.mkdir()

        adapter = ClaudeMemAdapter()
        result = adapter.migrate(source, workdir)
        assert result["status"] == "ok"
        target = workdir / "claude-mem-fixture.db"
        assert target.exists()
        assert target.read_bytes() == b"fake sqlite blob"

    def test_env_override_takes_precedence(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        source = tmp_path / "source.db"
        source.write_bytes(b"fake")
        workdir = tmp_path / "wd"
        workdir.mkdir()
        override = tmp_path / "elsewhere.db"

        monkeypatch.setenv("CLAUDE_MEM_DB", str(override))
        adapter = ClaudeMemAdapter()
        result = adapter.migrate(source, workdir)
        assert result["search_db"] == str(override)

    def test_returns_error_on_copy_failure(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        source = tmp_path / "missing.db"  # not created
        workdir = tmp_path / "wd"
        workdir.mkdir()
        adapter = ClaudeMemAdapter()
        result = adapter.migrate(source, workdir)
        assert result["status"] == "error"
