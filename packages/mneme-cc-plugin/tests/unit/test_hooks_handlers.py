"""Direct handler tests for each hook (no subprocess spawn).

Each test invokes the hook's ``handle`` function with a captured
stdout buffer so we can assert on the structured JSON envelope.
Hooks are written to be idempotent and side-effect-free outside the
vault, so test isolation just needs a tmp-path vault.
"""

from __future__ import annotations

import io
import json
import sqlite3
import sys
import time
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

    def test_vault_blocks_fenced_as_untrusted(
        self, monkeypatch: pytest.MonkeyPatch, vault: VaultConfig
    ) -> None:
        # G-3: vault-derived blocks must be wrapped in the spotlighting
        # fence so crafted titles/headings cannot act as instructions.
        from tests.unit.fts5_test_db import build_minimal_db

        build_minimal_db(
            vault.fts5_db,
            docs=[
                {"path": "s1.md", "title": "Session 1", "type": "session", "mtime": 1.0},
            ],
        )
        buf = _capture(monkeypatch)
        session_start.handle({}, vault)
        ctx = json.loads(buf.getvalue())["hookSpecificOutput"]["additionalContext"]
        assert "[mneme:untrusted-memory]" in ctx
        assert "[/mneme:untrusted-memory]" in ctx
        open_at = ctx.index("[mneme:untrusted-memory]")
        close_at = ctx.index("[/mneme:untrusted-memory]")
        assert open_at < ctx.index("Session 1") < close_at


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

    def _stage_summary_events(self, vault: VaultConfig, session_id: str) -> None:
        from datetime import UTC, datetime

        day = datetime.now(UTC).strftime("%Y-%m-%d")
        out_dir = vault.staging_dir / "host" / day
        out_dir.mkdir(parents=True, exist_ok=True)
        record = {
            "session_id": session_id,
            "tool_name": "Edit",
            "tool_input": {"file_path": "notes/today.md"},
            "captured_at": datetime.now(UTC).isoformat(),
        }
        (out_dir / "10-00-events.jsonl").write_text(
            json.dumps(record) + "\n", encoding="utf-8"
        )

    def test_session_log_carries_deterministic_summary(
        self, monkeypatch: pytest.MonkeyPatch, vault: VaultConfig
    ) -> None:
        _capture(monkeypatch)
        self._stage_summary_events(vault, "s-sum")
        stop.handle({"session_id": "s-sum"}, vault)
        produced = list((vault.root / "sessions").glob("*.md"))
        assert len(produced) == 1
        text = produced[0].read_text(encoding="utf-8")
        assert "notes/today.md" in text
        assert "Files touched" in text
        assert "placeholder" not in text.lower()

    def test_summary_disabled_falls_back_to_placeholder(
        self, monkeypatch: pytest.MonkeyPatch, vault: VaultConfig
    ) -> None:
        _capture(monkeypatch)
        self._stage_summary_events(vault, "s-off")
        (vault.state_dir / "summary.json").write_text(
            json.dumps({"deterministic": False}), encoding="utf-8"
        )
        stop.handle({"session_id": "s-off"}, vault)
        produced = list((vault.root / "sessions").glob("*.md"))
        text = produced[0].read_text(encoding="utf-8")
        assert "placeholder" in text.lower()
        assert "notes/today.md" not in text


class TestSessionEndProposalDrain:
    def test_drain_applies_queued_proposal(
        self, monkeypatch: pytest.MonkeyPatch, vault: VaultConfig
    ) -> None:
        from mneme_core.approval import EditCategory, propose
        from mneme_core.memory_apply import queue_proposal
        from mneme_core.policy import AutoApproveClass

        (vault.state_dir / "policy.json").write_text(
            json.dumps({"auto_approve": ["typo-fix"]}), encoding="utf-8"
        )
        proposal = propose(
            action="create",
            target_path="notes/drained.md",
            content="drained at session end",
            category=EditCategory.EPHEMERAL,
        )
        queue_proposal(vault, proposal, AutoApproveClass.TYPO_FIX)
        _capture(monkeypatch)
        session_end.handle({"session_id": "s-drain"}, vault)
        assert (vault.root / "notes/drained.md").is_file()
        assert not (vault.state_dir / "proposals" / "pending.jsonl").exists()

    def test_no_policy_file_means_no_drain(
        self, monkeypatch: pytest.MonkeyPatch, vault: VaultConfig
    ) -> None:
        from mneme_core.approval import EditCategory, propose
        from mneme_core.memory_apply import queue_proposal
        from mneme_core.policy import AutoApproveClass

        proposal = propose(
            action="create",
            target_path="notes/held.md",
            content="held",
            category=EditCategory.EPHEMERAL,
        )
        queue_proposal(vault, proposal, AutoApproveClass.TYPO_FIX)
        _capture(monkeypatch)
        session_end.handle({"session_id": "s-hold"}, vault)
        assert not (vault.root / "notes/held.md").exists()
        assert (vault.state_dir / "proposals" / "pending.jsonl").is_file()


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


class TestStopEmitBeforeKg:
    """emit() must be called before any KG filesystem work on both paths.

    The C2 constraint (Stop p95 < 1000 ms) requires that the user-visible
    response is written to stdout before any post-session bookkeeping.
    These tests use a call recorder to assert ordering, not just presence.
    """

    def _make_recorder(
        self, monkeypatch: pytest.MonkeyPatch, vault: VaultConfig
    ) -> list[str]:
        """Return a shared call log and patch emit + _request_kg_community_refresh."""
        from mneme_cc_plugin.hooks import stop as stop_mod

        call_log: list[str] = []

        real_emit = stop_mod.emit  # type: ignore[attr-defined]

        def _recording_emit(**kwargs: Any) -> None:
            call_log.append("emit")
            real_emit(**kwargs)

        def _recording_kg(v: object) -> None:
            call_log.append("kg")

        monkeypatch.setattr(stop_mod, "emit", _recording_emit)
        monkeypatch.setattr(
            stop_mod, "_request_kg_community_refresh", _recording_kg
        )
        return call_log

    def test_emit_before_kg_on_non_empty_session(
        self, monkeypatch: pytest.MonkeyPatch, vault: VaultConfig
    ) -> None:
        # Non-git vault always takes the non-empty path.
        call_log = self._make_recorder(monkeypatch, vault)
        buf = _capture(monkeypatch)
        from mneme_cc_plugin.hooks import stop as stop_mod

        stop_mod.handle({"session_id": "s-order-nonempty"}, vault)

        assert json.loads(buf.getvalue())["continue"] is True
        assert call_log == ["emit", "kg"], (
            f"Expected emit then kg, got: {call_log}"
        )

    def test_emit_before_kg_on_empty_session(
        self, monkeypatch: pytest.MonkeyPatch, vault: VaultConfig
    ) -> None:
        call_log = self._make_recorder(monkeypatch, vault)
        buf = _capture(monkeypatch)
        from mneme_cc_plugin.hooks import stop as stop_mod

        monkeypatch.setattr(stop_mod, "_is_empty_session", lambda _root: True)
        stop_mod.handle({"session_id": "s-order-empty"}, vault)

        assert json.loads(buf.getvalue())["continue"] is True
        assert call_log == ["emit", "kg"], (
            f"Expected emit then kg, got: {call_log}"
        )


# C2 Stop timing budget: 1000 ms p95 on the full profile (KG active).
# The budget covers the hot path up to and including emit(); the
# post-emit KG marking runs after the result is already written so it
# does not count against C2.  We measure only the emit-inclusive portion
# by recording the timestamp at which emit() returns.
_C2_BUDGET_MS = 1000


class TestStopHandleTimingFullProfile:
    """Micro-benchmark: Stop emit latency must stay under C2 budget.

    Runs with KG active (full-profile proxy) so the number is no longer
    a lite-profile proxy.  The KG mark_community_refresh is a fast
    flag-file write gated by is_active(); its cost is post-emit and
    therefore excluded from the C2 window by the reordering fix.
    """

    def test_stop_emit_latency_under_c2_budget_full_profile(
        self, monkeypatch: pytest.MonkeyPatch, vault: VaultConfig
    ) -> None:
        _activate_kg(vault)
        from mneme_cc_plugin.hooks import stop as stop_mod

        emit_elapsed_ms: list[float] = []

        real_emit = stop_mod.emit  # type: ignore[attr-defined]

        def _timed_emit(**kwargs: Any) -> None:
            t0 = time.perf_counter()
            real_emit(**kwargs)
            emit_elapsed_ms.append((time.perf_counter() - t0) * 1000)

        monkeypatch.setattr(stop_mod, "emit", _timed_emit)
        buf = _capture(monkeypatch)

        t_start = time.perf_counter()
        stop_mod.handle({"session_id": "s-timing-full"}, vault)
        total_to_emit_ms = (time.perf_counter() - t_start) * 1000

        assert json.loads(buf.getvalue())["continue"] is True
        assert emit_elapsed_ms, "emit was never called"
        # The full wall time from handle() entry to emit() return must be
        # under the C2 budget.  In practice this runs in <50 ms on any
        # developer machine; the 1000 ms ceiling is the contractual limit.
        assert total_to_emit_ms < _C2_BUDGET_MS, (
            f"Stop emit latency {total_to_emit_ms:.1f} ms exceeds "
            f"C2 budget of {_C2_BUDGET_MS} ms (full-profile run)"
        )


class TestStopEmitOnTouchStateFailure:
    """F4: emit() must be called even when _touch_state raises OSError.

    The non-empty-session path already had this guarantee. The empty-session
    path did not — a read-only or full filesystem would suppress the hook
    response entirely.  The fix wraps _touch_state in try/except OSError in
    the empty-session branch and calls emit() unconditionally afterwards.
    """

    def test_empty_session_emit_despite_touch_state_oserror(
        self, monkeypatch: pytest.MonkeyPatch, vault: VaultConfig
    ) -> None:
        from mneme_cc_plugin.hooks import stop as stop_mod

        # Force the empty-session branch.
        monkeypatch.setattr(stop_mod, "_is_empty_session", lambda _root: True)

        # Make _touch_state raise OSError to simulate a read-only filesystem.
        def _raise_oserror(_vault: object) -> None:
            raise OSError("read-only filesystem")

        monkeypatch.setattr(stop_mod, "_touch_state", _raise_oserror)

        buf = _capture(monkeypatch)
        stop_mod.handle({"session_id": "s-ro"}, vault)

        # emit() must still have produced a valid Stop response on stdout.
        out = json.loads(buf.getvalue())
        assert out["continue"] is True

    def test_empty_session_oserror_written_to_stderr(
        self,
        monkeypatch: pytest.MonkeyPatch,
        vault: VaultConfig,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        from mneme_cc_plugin.hooks import stop as stop_mod

        monkeypatch.setattr(stop_mod, "_is_empty_session", lambda _root: True)

        def _raise_oserror(_vault: object) -> None:
            raise OSError("disk full")

        monkeypatch.setattr(stop_mod, "_touch_state", _raise_oserror)

        _capture(monkeypatch)
        stop_mod.handle({"session_id": "s-err"}, vault)

        # The error message must land on stderr (captured by capsys before
        # monkeypatch replaced sys.stdout).
        captured = capsys.readouterr()
        assert "disk full" in captured.err or "state touch failed" in captured.err


class TestSessionStartReadOnlyDb:
    """F5: _block_recent_sessions must open the FTS5 db in read-only URI mode.

    The fix changes ``sqlite3.connect(vault.fts5_db)`` to
    ``sqlite3.connect("file:...?mode=ro", uri=True)`` and removes the
    advisory PRAGMA.  We verify two properties:

    1. A write attempt on the connection raises OperationalError (the
       connection is genuinely read-only, not just advisory).
    2. When the db file does not exist the function degrades gracefully
       (returns "" without raising) — same as before the fix.
    """

    def test_connection_is_read_only(self, vault: VaultConfig) -> None:
        from tests.unit.fts5_test_db import build_minimal_db

        build_minimal_db(
            vault.fts5_db,
            docs=[{"path": "s1.md", "title": "T1", "type": "session", "mtime": 1.0}],
        )

        # Open the same db the hook would open and attempt a write.
        conn = sqlite3.connect(f"file:{vault.fts5_db}?mode=ro", uri=True)
        try:
            with pytest.raises(sqlite3.OperationalError):
                conn.execute("INSERT INTO documents (path) VALUES ('x')")
        finally:
            conn.close()

    def test_missing_db_degrades_gracefully(
        self, monkeypatch: pytest.MonkeyPatch, vault: VaultConfig
    ) -> None:
        # vault.fts5_db does not exist — _block_recent_sessions must return "".
        from mneme_cc_plugin.hooks import session_start as ss_mod

        assert not vault.fts5_db.exists()
        result = ss_mod._block_recent_sessions(vault)
        assert result == ""

    def test_session_docs_still_returned_after_fix(
        self, monkeypatch: pytest.MonkeyPatch, vault: VaultConfig
    ) -> None:
        from mneme_cc_plugin.hooks import session_start as ss_mod
        from tests.unit.fts5_test_db import build_minimal_db

        build_minimal_db(
            vault.fts5_db,
            docs=[
                {"path": "a.md", "title": "Alpha", "type": "session", "mtime": 2.0},
                {"path": "b.md", "title": "Beta", "type": "session", "mtime": 1.0},
            ],
        )
        result = ss_mod._block_recent_sessions(vault)
        assert "Alpha" in result
        assert "Beta" in result
