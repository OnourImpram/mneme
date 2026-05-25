"""Unit tests for MNEME_SKIP_HOOKS selective-bypass logic."""

from __future__ import annotations

import io
import json
import sys
from typing import Any

import pytest

from mneme_cc_plugin.hooks import lib as hook_lib


@pytest.fixture(autouse=True)
def _clear_skip_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(hook_lib.SKIP_HOOKS_ENV, raising=False)
    monkeypatch.delenv(hook_lib.KILL_SWITCH_ENV, raising=False)


class TestIsSkipped:
    # --- skip-all sentinels ---

    @pytest.mark.parametrize("value", ["all", "ALL", "All", "1", "true", "True"])
    def test_skip_all_sentinels(
        self, value: str, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv(hook_lib.SKIP_HOOKS_ENV, value)
        assert hook_lib.is_skipped("Stop") is True
        assert hook_lib.is_skipped("SessionStart") is True
        assert hook_lib.is_skipped("PostToolUse") is True

    # --- comma-separated list ---

    def test_specific_event_is_skipped(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(hook_lib.SKIP_HOOKS_ENV, "Stop,SessionStart")
        assert hook_lib.is_skipped("Stop") is True
        assert hook_lib.is_skipped("SessionStart") is True

    def test_other_event_is_not_skipped(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(hook_lib.SKIP_HOOKS_ENV, "Stop,SessionStart")
        assert hook_lib.is_skipped("PostToolUse") is False
        assert hook_lib.is_skipped("PreCompact") is False
        assert hook_lib.is_skipped("SessionEnd") is False

    def test_case_insensitive_matching(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(hook_lib.SKIP_HOOKS_ENV, "stop,SESSIONSTART")
        assert hook_lib.is_skipped("Stop") is True
        assert hook_lib.is_skipped("SessionStart") is True

    def test_single_event_in_list(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(hook_lib.SKIP_HOOKS_ENV, "PostToolUse")
        assert hook_lib.is_skipped("PostToolUse") is True
        assert hook_lib.is_skipped("Stop") is False

    def test_whitespace_around_entries(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(hook_lib.SKIP_HOOKS_ENV, " Stop , SessionEnd ")
        assert hook_lib.is_skipped("Stop") is True
        assert hook_lib.is_skipped("SessionEnd") is True
        assert hook_lib.is_skipped("PostToolUse") is False

    # --- not set ---

    def test_unset_returns_false(self) -> None:
        assert hook_lib.is_skipped("Stop") is False
        assert hook_lib.is_skipped("SessionStart") is False

    def test_empty_string_returns_false(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv(hook_lib.SKIP_HOOKS_ENV, "")
        assert hook_lib.is_skipped("Stop") is False


class TestRunHookSkip:
    """Integration tests: run_hook short-circuits on MNEME_SKIP_HOOKS."""

    def _run(
        self,
        monkeypatch: pytest.MonkeyPatch,
        *,
        skip_env: str,
        hook_event_name: str,
    ) -> tuple[int, dict[str, Any], int]:
        """Run run_hook and return (exit_code, stdout_payload, handler_call_count)."""
        monkeypatch.setenv(hook_lib.SKIP_HOOKS_ENV, skip_env)
        monkeypatch.setattr(sys, "stdin", io.StringIO(""))
        buf = io.StringIO()
        monkeypatch.setattr(sys, "stdout", buf)
        call_count = {"n": 0}

        def handler(event: dict[str, Any], vault: Any) -> None:
            call_count["n"] += 1

        code = hook_lib.run_hook(handler, hook_event_name=hook_event_name)
        payload: dict[str, Any] = json.loads(buf.getvalue())
        return code, payload, call_count["n"]

    def test_skip_all_short_circuits_handler(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        code, payload, calls = self._run(
            monkeypatch, skip_env="all", hook_event_name="Stop"
        )
        assert code == 0
        assert calls == 0
        assert payload == {"continue": True, "suppressOutput": True}

    def test_skip_specific_event_short_circuits(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        code, payload, calls = self._run(
            monkeypatch,
            skip_env="Stop,SessionStart",
            hook_event_name="Stop",
        )
        assert code == 0
        assert calls == 0
        assert payload["continue"] is True

    def test_non_skipped_event_calls_handler(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv(hook_lib.SKIP_HOOKS_ENV, "Stop,SessionStart")
        monkeypatch.setattr(sys, "stdin", io.StringIO(""))
        buf = io.StringIO()
        monkeypatch.setattr(sys, "stdout", buf)
        call_count = {"n": 0}

        def handler(event: dict[str, Any], vault: Any) -> None:
            call_count["n"] += 1
            hook_lib.emit(hook_event_name="PostToolUse")

        code = hook_lib.run_hook(handler, hook_event_name="PostToolUse")
        assert code == 0
        assert call_count["n"] == 1

    def test_skip_env_not_set_calls_handler(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(sys, "stdin", io.StringIO(""))
        buf = io.StringIO()
        monkeypatch.setattr(sys, "stdout", buf)
        call_count = {"n": 0}

        def handler(event: dict[str, Any], vault: Any) -> None:
            call_count["n"] += 1
            hook_lib.emit(hook_event_name="PreCompact")

        code = hook_lib.run_hook(handler, hook_event_name="PreCompact")
        assert code == 0
        assert call_count["n"] == 1
