"""Unit tests for cce.triggers — should_checkpoint and state helpers."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from mneme_core.cce.config import CceConfig
from mneme_core.cce.triggers import (
    increment_event_counter,
    read_cce_state,
    should_checkpoint,
    update_cce_state,
)


def _cfg(
    threshold: float = 0.65,
    min_interval: int = 20,
) -> CceConfig:
    return CceConfig(
        enabled=True,
        context_threshold_pct=threshold,
        min_checkpoint_interval_events=min_interval,
    )


class TestShouldCheckpoint:
    def test_threshold_triggers_above_min_interval(self) -> None:
        trigger, reason = should_checkpoint(
            fill_pct=0.70,
            config=_cfg(),
            event={},
            events_since_last=20,
        )
        assert trigger is True
        assert reason == "threshold"

    def test_threshold_does_not_trigger_below_min_interval(self) -> None:
        trigger, reason = should_checkpoint(
            fill_pct=0.70,
            config=_cfg(),
            event={},
            events_since_last=5,
        )
        assert trigger is False
        assert reason == "debounced"

    def test_exactly_at_threshold_triggers(self) -> None:
        trigger, reason = should_checkpoint(
            fill_pct=0.65,
            config=_cfg(threshold=0.65),
            event={},
            events_since_last=20,
        )
        assert trigger is True

    def test_below_threshold_no_trigger(self) -> None:
        trigger, _ = should_checkpoint(
            fill_pct=0.50,
            config=_cfg(),
            event={},
            events_since_last=20,
        )
        assert trigger is False

    def test_git_commit_triggers(self) -> None:
        event = {
            "tool_name": "Bash",
            "tool_input": {"command": "git commit -m 'wip'"},
        }
        trigger, reason = should_checkpoint(
            fill_pct=0.0,
            config=_cfg(),
            event=event,
            events_since_last=20,
        )
        assert trigger is True
        assert reason == "git_commit"

    def test_git_commit_debounced_below_interval(self) -> None:
        event = {
            "tool_name": "Bash",
            "tool_input": {"command": "git commit -m 'wip'"},
        }
        trigger, reason = should_checkpoint(
            fill_pct=0.0,
            config=_cfg(),
            event=event,
            events_since_last=5,
        )
        assert trigger is False
        assert reason == "debounced"

    def test_large_response_triggers(self) -> None:
        big_payload = {"output": "x" * 9000}
        event = {"tool_name": "Bash", "tool_response": big_payload}
        trigger, reason = should_checkpoint(
            fill_pct=0.0,
            config=_cfg(),
            event=event,
            events_since_last=20,
        )
        assert trigger is True
        assert reason == "large_response"

    def test_small_response_no_trigger(self) -> None:
        event = {"tool_name": "Bash", "tool_response": {"output": "ok"}}
        trigger, _ = should_checkpoint(
            fill_pct=0.0,
            config=_cfg(),
            event=event,
            events_since_last=20,
        )
        assert trigger is False

    def test_explicit_remember_this_bypasses_debounce(self) -> None:
        event = {
            "hook_event_name": "UserPromptSubmit",
            "prompt": "remember this for later",
        }
        trigger, reason = should_checkpoint(
            fill_pct=0.0,
            config=_cfg(),
            event=event,
            events_since_last=0,  # debounce would normally block
        )
        assert trigger is True
        assert reason == "explicit"

    def test_explicit_hatirla_bypasses_debounce(self) -> None:
        event = {
            "hook_event_name": "UserPromptSubmit",
            "prompt": "Bunu hatirla lutfen",
        }
        trigger, reason = should_checkpoint(
            fill_pct=0.0,
            config=_cfg(),
            event=event,
            events_since_last=0,
        )
        assert trigger is True
        assert reason == "explicit"

    def test_explicit_keyword_only_fires_on_user_prompt_submit(self) -> None:
        # Same prompt text on a different hook event should NOT bypass debounce.
        event = {
            "hook_event_name": "PostToolUse",
            "prompt": "remember this for later",
        }
        trigger, reason = should_checkpoint(
            fill_pct=0.0,
            config=_cfg(),
            event=event,
            events_since_last=0,
        )
        assert trigger is False
        assert reason == "debounced"

    def test_no_trigger_when_nothing_salient(self) -> None:
        trigger, reason = should_checkpoint(
            fill_pct=0.30,
            config=_cfg(),
            event={"tool_name": "Read"},
            events_since_last=100,
        )
        assert trigger is False
        assert reason == ""


class TestStateHelpers:
    def _state_dir(self, tmp_path: Path) -> Path:
        d = tmp_path / ".mneme"
        d.mkdir(parents=True, exist_ok=True)
        return d

    def test_read_missing_state_returns_empty(self, tmp_path: Path) -> None:
        state = read_cce_state(self._state_dir(tmp_path))
        assert isinstance(state, dict)

    def test_update_and_read_round_trip(self, tmp_path: Path) -> None:
        sd = self._state_dir(tmp_path)
        update_cce_state(sd, last_checkpoint_anchor="abc123", events_since_checkpoint=5)
        state = read_cce_state(sd)
        assert state["last_checkpoint_anchor"] == "abc123"
        assert state["events_since_checkpoint"] == 5

    def test_update_preserves_other_fields(self, tmp_path: Path) -> None:
        sd = self._state_dir(tmp_path)
        # Write an existing state.json with an unrelated field.
        (sd / "state.json").write_text(
            json.dumps({"last_session_end_at": "2026-01-01T00:00:00"}),
            encoding="utf-8",
        )
        update_cce_state(sd, events_since_checkpoint=3)
        state = read_cce_state(sd)
        assert state["last_session_end_at"] == "2026-01-01T00:00:00"
        assert state["events_since_checkpoint"] == 3

    def test_increment_starts_at_one(self, tmp_path: Path) -> None:
        sd = self._state_dir(tmp_path)
        val = increment_event_counter(sd)
        assert val == 1

    def test_increment_accumulates(self, tmp_path: Path) -> None:
        sd = self._state_dir(tmp_path)
        for expected in range(1, 6):
            val = increment_event_counter(sd)
            assert val == expected

    def test_increment_after_reset(self, tmp_path: Path) -> None:
        sd = self._state_dir(tmp_path)
        for _ in range(5):
            increment_event_counter(sd)
        update_cce_state(sd, events_since_checkpoint=0)
        val = increment_event_counter(sd)
        assert val == 1
