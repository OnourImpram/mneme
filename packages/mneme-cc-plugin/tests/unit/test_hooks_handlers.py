"""Direct handler tests for each hook (no subprocess spawn).

Each test invokes the hook's ``handle`` function with a captured
stdout buffer so we can assert on the structured JSON envelope.
Hooks are written to be idempotent and side-effect-free outside the
vault, so test isolation just needs a tmp-path vault.
"""

from __future__ import annotations

import io
import json
import sys
from pathlib import Path
from typing import Any

import pytest
from mneme_core.vault.config import VaultConfig

from mneme_cc_plugin.hooks import (
    post_tool_use,
    pre_compact,
    session_end,
    session_start,
    stop,
)


@pytest.fixture
def vault(tmp_path: Path) -> VaultConfig:
    VaultConfig.fromPath = VaultConfig.from_path  # alias
    v = VaultConfig.from_path(tmp_path)
    (v.root / ".mneme").mkdir(parents=True, exist_ok=True)
    return v


def _capture(monkeypatch: pytest.MonkeyPatch) -> io.StringIO:
    buf = io.StringIO()
    monkeypatch.setattr(sys, "stdout", buf)
    return buf


class TestPostToolUseHandle:
    def test_no_vault_returns_continue(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        buf = _capture(monkeypatch)
        post_tool_use.handle({"tool_name": "Edit"}, None)
        out = json.loads(buf.getvalue())
        assert out["continue"] is True

    def test_event_with_captured_tool_writes_staging(
        self, monkeypatch: pytest.MonkeyPatch, vault: VaultConfig
    ) -> None:
        buf = _capture(monkeypatch)
        event: dict[str, Any] = {
            "tool_name": "Edit",
            "session_id": "test-session",
            "tool_input": {"path": "x.md", "old_string": "a", "new_string": "b"},
            "tool_response": {"ok": True},
        }
        post_tool_use.handle(event, vault)
        out = json.loads(buf.getvalue())
        assert out["continue"] is True
        # Staging dir should have at least one JSONL file under host subdir.
        produced = list(vault.staging_dir.rglob("*.jsonl"))
        assert len(produced) >= 1

    def test_event_with_unmatched_tool_skipped(
        self, monkeypatch: pytest.MonkeyPatch, vault: VaultConfig
    ) -> None:
        _capture(monkeypatch)
        post_tool_use.handle({"tool_name": "Glob"}, vault)
        # Glob is not in DEFAULT_CAPTURE_TOOLS, so nothing should be staged.
        assert list(vault.staging_dir.rglob("*.jsonl")) == []


class TestSessionStartHandle:
    def test_no_vault_emits_blank_continue(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        buf = _capture(monkeypatch)
        session_start.handle({}, None)
        out = json.loads(buf.getvalue())
        assert "hookSpecificOutput" not in out

    def test_with_vault_emits_context(
        self, monkeypatch: pytest.MonkeyPatch, vault: VaultConfig
    ) -> None:
        buf = _capture(monkeypatch)
        session_start.handle({}, vault)
        out = json.loads(buf.getvalue())
        assert "hookSpecificOutput" in out
        ctx = out["hookSpecificOutput"]["additionalContext"]
        assert "## Vault Context" in ctx

    def test_includes_session_docs_when_present(
        self, monkeypatch: pytest.MonkeyPatch, vault: VaultConfig
    ) -> None:
        # Build a minimal FTS5 db with a session-typed doc.
        from tests.unit.fts5_test_db import build_minimal_db

        build_minimal_db(
            vault.fts5_db,
            docs=[
                {"path": "s1.md", "title": "Session 1", "type": "session", "mtime": 1.0},
                {"path": "s2.md", "title": "Session 2", "type": "session", "mtime": 2.0},
            ],
        )
        buf = _capture(monkeypatch)
        session_start.handle({}, vault)
        ctx = json.loads(buf.getvalue())["hookSpecificOutput"]["additionalContext"]
        assert "Session 1" in ctx
        assert "Session 2" in ctx


class TestStopHandle:
    def test_no_vault_returns_continue(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        buf = _capture(monkeypatch)
        stop.handle({}, None)
        out = json.loads(buf.getvalue())
        assert out["continue"] is True

    def test_writes_session_log_when_vault_non_git(
        self, monkeypatch: pytest.MonkeyPatch, vault: VaultConfig
    ) -> None:
        # Non-git vault always logs.
        _capture(monkeypatch)
        stop.handle({"session_id": "s-test"}, vault)
        log_dir = vault.root / "sessions"
        produced = list(log_dir.glob("*.md"))
        assert len(produced) == 1
        text = produced[0].read_text(encoding="utf-8")
        assert "s-test" in text
        assert "session" in text.lower()

    def test_state_file_stamped(
        self, monkeypatch: pytest.MonkeyPatch, vault: VaultConfig
    ) -> None:
        _capture(monkeypatch)
        stop.handle({"session_id": "s-state"}, vault)
        state_path = vault.state_dir / "state.json"
        assert state_path.exists()
        state = json.loads(state_path.read_text(encoding="utf-8"))
        assert "last_session_end_at" in state


class TestPreCompactHandle:
    def test_no_vault_returns_continue(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        buf = _capture(monkeypatch)
        pre_compact.handle({}, None)
        assert json.loads(buf.getvalue())["continue"] is True

    def test_stamps_last_precompact_at(
        self, monkeypatch: pytest.MonkeyPatch, vault: VaultConfig
    ) -> None:
        _capture(monkeypatch)
        pre_compact.handle({}, vault)
        state = json.loads(
            (vault.state_dir / "state.json").read_text(encoding="utf-8")
        )
        assert "last_precompact_at" in state


class TestSessionEndHandle:
    def test_no_vault_returns_continue(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        buf = _capture(monkeypatch)
        session_end.handle({}, None)
        assert json.loads(buf.getvalue())["continue"] is True

    def test_stamps_last_session_end_at(
        self, monkeypatch: pytest.MonkeyPatch, vault: VaultConfig
    ) -> None:
        _capture(monkeypatch)
        session_end.handle({}, vault)
        state = json.loads(
            (vault.state_dir / "state.json").read_text(encoding="utf-8")
        )
        assert "last_session_end_at" in state


def _activate_kg(vault: VaultConfig) -> None:
    vault.kg_active_flag.parent.mkdir(parents=True, exist_ok=True)
    vault.kg_active_flag.write_text("on\n", encoding="utf-8")


class TestPostToolUseKgWiring:
    def test_no_kg_when_flag_absent(
        self, monkeypatch: pytest.MonkeyPatch, vault: VaultConfig
    ) -> None:
        _capture(monkeypatch)
        post_tool_use.handle(
            {"tool_name": "Edit", "tool_input": {"path": "x.md"}}, vault
        )
        # Without the active flag the KG queue should stay empty.
        assert not list(vault.kg_queue_dir.rglob("*.jsonl"))

    def test_stages_to_kg_queue_when_active(
        self, monkeypatch: pytest.MonkeyPatch, vault: VaultConfig
    ) -> None:
        _activate_kg(vault)
        _capture(monkeypatch)
        post_tool_use.handle(
            {"tool_name": "Edit", "tool_input": {"path": "x.md"}}, vault
        )
        produced = list(vault.kg_queue_dir.rglob("*-events.jsonl"))
        assert len(produced) >= 1

    def test_kg_failure_does_not_break_hook(
        self,
        monkeypatch: pytest.MonkeyPatch,
        vault: VaultConfig,
    ) -> None:
        _activate_kg(vault)

        # Make the KG stage_event raise. The hook must still emit a
        # benign continue envelope so Claude Code is unaffected.
        from mneme_cc_plugin.hooks import post_tool_use as pth

        def _boom(*_a: object, **_kw: object) -> None:
            raise RuntimeError("simulated kg failure")

        monkeypatch.setattr(pth, "stage_event", _boom)

        buf = _capture(monkeypatch)
        pth.handle(
            {"tool_name": "Edit", "tool_input": {"path": "x.md"}}, vault
        )
        assert json.loads(buf.getvalue())["continue"] is True


class TestStopHookKgWiring:
    def test_no_refresh_flag_when_kg_inactive(
        self, monkeypatch: pytest.MonkeyPatch, vault: VaultConfig
    ) -> None:
        _capture(monkeypatch)
        stop.handle({"session_id": "s-1"}, vault)
        assert not vault.kg_community_refresh_flag.exists()

    def test_refresh_flag_written_when_kg_active(
        self, monkeypatch: pytest.MonkeyPatch, vault: VaultConfig
    ) -> None:
        _activate_kg(vault)
        _capture(monkeypatch)
        stop.handle({"session_id": "s-2"}, vault)
        assert vault.kg_community_refresh_flag.is_file()
        payload = json.loads(
            vault.kg_community_refresh_flag.read_text(encoding="utf-8")
        )
        assert payload["reason"] == "stop_hook"

    def test_refresh_flag_written_even_on_empty_session(
        self, monkeypatch: pytest.MonkeyPatch, vault: VaultConfig
    ) -> None:
        _activate_kg(vault)
        # Force the empty-session branch by stubbing the helper.
        from mneme_cc_plugin.hooks import stop as stop_mod

        monkeypatch.setattr(stop_mod, "_is_empty_session", lambda _root: True)
        _capture(monkeypatch)
        stop_mod.handle({"session_id": "s-empty"}, vault)
        assert vault.kg_community_refresh_flag.is_file()

    def test_kg_failure_does_not_break_hook(
        self, monkeypatch: pytest.MonkeyPatch, vault: VaultConfig
    ) -> None:
        _activate_kg(vault)
        from mneme_cc_plugin.hooks import stop as stop_mod

        def _boom(*_a: object, **_kw: object) -> None:
            raise RuntimeError("simulated kg failure")

        monkeypatch.setattr(stop_mod, "mark_community_refresh", _boom)
        buf = _capture(monkeypatch)
        stop_mod.handle({"session_id": "s-3"}, vault)
        assert json.loads(buf.getvalue())["continue"] is True
