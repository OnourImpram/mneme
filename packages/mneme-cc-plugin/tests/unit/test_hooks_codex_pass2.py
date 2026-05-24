"""Regression tests for the Codex Pass 2 hook fixes.

Three behaviors land here:

  1. ``hooks/lock.py`` blocks a second acquirer until the first
     releases, and surfaces ``TimeoutError`` on the contention path.
  2. ``post_tool_use`` compresses Bash tool output before staging so
     downstream consumers see the reduced payload.
  3. ``session_end`` only spawns the background compression
     subprocess when all three gates pass (config flag, no pause
     flag, provider auth in env). Each gate is verified in isolation
     by replacing ``subprocess.Popen`` with a recorder.
"""

from __future__ import annotations

import json
import sys
import threading
import time
from pathlib import Path
from typing import Any

import pytest
from mneme_core.vault.config import VaultConfig

from mneme_cc_plugin.hooks import lock as lock_mod
from mneme_cc_plugin.hooks import post_tool_use, session_end


@pytest.fixture
def vault(tmp_path: Path) -> VaultConfig:
    v = VaultConfig.from_path(tmp_path)
    (v.root / ".mneme").mkdir(parents=True, exist_ok=True)
    return v


# --- file_lock ---------------------------------------------------------

class TestFileLock:
    def test_acquires_and_releases_cleanly(self, tmp_path: Path) -> None:
        lock_path = tmp_path / "demo.lock"
        with lock_mod.file_lock(lock_path):
            assert lock_path.is_file()
        # Lock released; second acquisition should succeed quickly.
        with lock_mod.file_lock(lock_path, timeout_s=1.0):
            pass

    def test_serializes_concurrent_acquirers(self, tmp_path: Path) -> None:
        lock_path = tmp_path / "serial.lock"
        order: list[str] = []

        def worker(label: str, hold_s: float) -> None:
            with lock_mod.file_lock(lock_path, timeout_s=5.0):
                order.append(f"{label}-enter")
                time.sleep(hold_s)
                order.append(f"{label}-exit")

        t1 = threading.Thread(target=worker, args=("a", 0.1))
        t2 = threading.Thread(target=worker, args=("b", 0.0))
        t1.start()
        # Give t1 a tiny head-start so the test does not depend on
        # thread scheduling order.
        time.sleep(0.02)
        t2.start()
        t1.join()
        t2.join()
        # Both workers must complete their enter/exit pair without
        # interleaving. Either order is valid (a-then-b or b-then-a).
        joined = ",".join(order)
        assert joined in (
            "a-enter,a-exit,b-enter,b-exit",
            "b-enter,b-exit,a-enter,a-exit",
        )

    def test_timeout_raises_when_lock_held(self, tmp_path: Path) -> None:
        lock_path = tmp_path / "timeout.lock"
        # Hold the lock from a background thread, then try to grab it
        # with a tiny timeout from the foreground.
        held = threading.Event()
        release = threading.Event()

        def holder() -> None:
            with lock_mod.file_lock(lock_path, timeout_s=5.0):
                held.set()
                release.wait(timeout=5.0)

        t = threading.Thread(target=holder)
        t.start()
        try:
            assert held.wait(timeout=2.0)
            with pytest.raises(TimeoutError):
                with lock_mod.file_lock(lock_path, timeout_s=0.2):
                    pass
        finally:
            release.set()
            t.join()


# --- PostToolUse shell compression ------------------------------------

class TestPostToolUseShellCompress:
    def test_compresses_long_bash_stdout(self) -> None:
        # Build a string with enough repetition to compress.
        noise = ("hello world\n" * 200) + ("foo\n" * 50)
        event: dict[str, Any] = {
            "tool_name": "Bash",
            "tool_input": {"command": "yes hello world"},
            "tool_response": {"stdout": noise, "exit_code": 0},
        }
        original_len = len(noise)
        post_tool_use._compress_bash_payload(event)
        new = event["tool_response"]["stdout"]
        assert isinstance(new, str)
        assert len(new) < original_len, (
            f"expected compression, got original={original_len} new={len(new)}"
        )

    def test_skips_short_payloads(self) -> None:
        # Below the 256-byte threshold the function leaves the value alone.
        small = "ls\nfile1\n"
        event = {
            "tool_name": "Bash",
            "tool_response": {"stdout": small},
        }
        post_tool_use._compress_bash_payload(event)
        assert event["tool_response"]["stdout"] == small

    def test_ignores_non_bash_tools(self) -> None:
        long_text = "x" * 10_000
        event = {
            "tool_name": "Edit",
            "tool_response": {"content": long_text},
        }
        post_tool_use._compress_bash_payload(event)
        assert event["tool_response"]["content"] == long_text

    def test_handles_missing_tool_response(self) -> None:
        event = {"tool_name": "Bash"}
        # Must not raise on absent response.
        post_tool_use._compress_bash_payload(event)


# --- SessionEnd compression launch ------------------------------------

class _PopenRecorder:
    def __init__(self) -> None:
        self.calls: list[tuple[list[str], dict[str, Any]]] = []

    def __call__(self, cmd: list[str], **kwargs: Any) -> Any:
        self.calls.append((cmd, kwargs))

        class _Stub:
            pid = 12345

        return _Stub()


class TestSessionEndCompressionLaunch:
    def _make_state(self, vault: VaultConfig) -> None:
        # Touch state dir so the SessionEnd write does not fail.
        vault.state_dir.mkdir(parents=True, exist_ok=True)

    def test_no_spawn_when_compression_disabled(
        self,
        vault: VaultConfig,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        self._make_state(vault)
        recorder = _PopenRecorder()
        monkeypatch.setattr(session_end.subprocess, "Popen", recorder)
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
        # No compression.json => disabled.
        session_end._maybe_launch_compression(vault)
        assert recorder.calls == []

    def test_no_spawn_when_pause_flag_present(
        self,
        vault: VaultConfig,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        self._make_state(vault)
        vault.compression_config_path.parent.mkdir(parents=True, exist_ok=True)
        vault.compression_config_path.write_text(
            json.dumps({"enabled": True}), encoding="utf-8"
        )
        vault.compression_pause_flag.write_text("paused", encoding="utf-8")
        recorder = _PopenRecorder()
        monkeypatch.setattr(session_end.subprocess, "Popen", recorder)
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
        session_end._maybe_launch_compression(vault)
        assert recorder.calls == []

    def test_no_spawn_when_no_provider_auth(
        self,
        vault: VaultConfig,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        self._make_state(vault)
        vault.compression_config_path.parent.mkdir(parents=True, exist_ok=True)
        vault.compression_config_path.write_text(
            json.dumps({"enabled": True}), encoding="utf-8"
        )
        recorder = _PopenRecorder()
        monkeypatch.setattr(session_end.subprocess, "Popen", recorder)
        for var in (
            "ANTHROPIC_API_KEY",
            "OPENAI_API_KEY",
            "MNEME_LLM_API_KEY",
        ):
            monkeypatch.delenv(var, raising=False)
        session_end._maybe_launch_compression(vault)
        assert recorder.calls == []

    def test_spawns_subprocess_when_all_gates_pass(
        self,
        vault: VaultConfig,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        self._make_state(vault)
        vault.compression_config_path.parent.mkdir(parents=True, exist_ok=True)
        vault.compression_config_path.write_text(
            json.dumps({"enabled": True}), encoding="utf-8"
        )
        recorder = _PopenRecorder()
        monkeypatch.setattr(session_end.subprocess, "Popen", recorder)
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
        session_end._maybe_launch_compression(vault)
        assert len(recorder.calls) == 1
        cmd, kwargs = recorder.calls[0]
        assert cmd[1:] == ["-m", "mneme_core.cli", "compress", "run"]
        # Detached so the hook returns immediately.
        if sys.platform == "win32":
            assert "creationflags" in kwargs
        else:
            assert kwargs.get("start_new_session") is True
