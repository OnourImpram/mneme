"""Unit tests for the shared hook plumbing."""

from __future__ import annotations

import io
import json
import os
import sys
from typing import Any

import pytest

from mneme_cc_plugin.hooks import lib as hook_lib


@pytest.fixture(autouse=True)
def _clear_kill_switch(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(hook_lib.KILL_SWITCH_ENV, raising=False)


class TestIsDisabled:
    @pytest.mark.parametrize("value", ["1", "true", "True", "yes", "on"])
    def test_truthy_values(
        self, value: str, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv(hook_lib.KILL_SWITCH_ENV, value)
        assert hook_lib.is_disabled() is True

    @pytest.mark.parametrize("value", ["0", "false", "no", "", "anything-else"])
    def test_falsy_values(
        self, value: str, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv(hook_lib.KILL_SWITCH_ENV, value)
        assert hook_lib.is_disabled() is False

    def test_unset_is_false(self) -> None:
        assert hook_lib.is_disabled() is False


class TestReadEvent:
    def test_returns_empty_dict_for_tty(self, monkeypatch: pytest.MonkeyPatch) -> None:
        class FakeTty:
            def isatty(self) -> bool:
                return True

            def read(self) -> str:
                return ""

        monkeypatch.setattr(sys, "stdin", FakeTty())
        assert hook_lib.read_event() == {}

    def test_returns_empty_dict_for_blank_stdin(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(sys, "stdin", io.StringIO("   "))
        assert hook_lib.read_event() == {}

    def test_parses_valid_json(self, monkeypatch: pytest.MonkeyPatch) -> None:
        payload = {"tool_name": "Edit", "session_id": "abc"}
        monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(payload)))
        assert hook_lib.read_event() == payload

    def test_returns_empty_dict_for_malformed_json(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(sys, "stdin", io.StringIO("{not json"))
        assert hook_lib.read_event() == {}

    def test_returns_empty_dict_for_non_object_root(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(sys, "stdin", io.StringIO("[1,2,3]"))
        assert hook_lib.read_event() == {}


class TestEmit:
    def _captured(
        self, monkeypatch: pytest.MonkeyPatch, **kwargs: Any
    ) -> dict[str, Any]:
        buf = io.StringIO()
        monkeypatch.setattr(sys, "stdout", buf)
        hook_lib.emit(**kwargs)
        return json.loads(buf.getvalue())

    def test_defaults_continue_true_suppress_true(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        out = self._captured(monkeypatch)
        assert out == {"continue": True, "suppressOutput": True}

    def test_additional_context_wrapped(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        out = self._captured(
            monkeypatch,
            additional_context="hello",
            hook_event_name="SessionStart",
        )
        assert out["hookSpecificOutput"] == {
            "hookEventName": "SessionStart",
            "additionalContext": "hello",
        }

    def test_system_message_passthrough(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        out = self._captured(monkeypatch, system_message="warning")
        assert out["systemMessage"] == "warning"


class TestRunHook:
    def test_kill_switch_short_circuits(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv(hook_lib.KILL_SWITCH_ENV, "1")
        buf = io.StringIO()
        monkeypatch.setattr(sys, "stdout", buf)
        monkeypatch.setattr(sys, "stdin", io.StringIO(""))
        called = {"n": 0}

        def handler(event: dict[str, Any], vault: Any) -> None:
            called["n"] += 1

        exit_code = hook_lib.run_hook(handler, hook_event_name="X")
        assert exit_code == 0
        assert called["n"] == 0
        out = json.loads(buf.getvalue())
        assert out == {"continue": True, "suppressOutput": True}

    def test_exception_in_handler_does_not_crash(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(sys, "stdin", io.StringIO('{"tool_name": "X"}'))
        buf_out = io.StringIO()
        buf_err = io.StringIO()
        monkeypatch.setattr(sys, "stdout", buf_out)
        monkeypatch.setattr(sys, "stderr", buf_err)

        def handler(event: dict[str, Any], vault: Any) -> None:
            raise RuntimeError("boom")

        exit_code = hook_lib.run_hook(handler, hook_event_name="X")
        assert exit_code == 0
        out = json.loads(buf_out.getvalue())
        assert out["continue"] is True
        assert "boom" in buf_err.getvalue()

    def test_handler_receives_parsed_event_and_vault(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path
    ) -> None:
        # Set vault env so resolve_vault succeeds.
        monkeypatch.setenv("MNEME_VAULT", str(tmp_path))
        monkeypatch.setattr(
            sys, "stdin", io.StringIO('{"tool_name": "Edit"}')
        )
        monkeypatch.setattr(sys, "stdout", io.StringIO())

        seen = {}

        def handler(event: dict[str, Any], vault: Any) -> None:
            seen["event"] = event
            seen["vault_root"] = str(vault.root) if vault is not None else None
            hook_lib.emit(hook_event_name="X")

        hook_lib.run_hook(handler, hook_event_name="X")
        assert seen["event"] == {"tool_name": "Edit"}
        assert seen["vault_root"] is not None
        assert os.path.samefile(seen["vault_root"], str(tmp_path))
