"""Stop hook network, LLM, contention, and real p95 guards."""

from __future__ import annotations

import http.client
import io
import json
import math
import socket
import sys
import threading
import time
import urllib.request
from datetime import UTC, datetime
from pathlib import Path
from typing import NoReturn

import pytest
from mneme_core.compression import pipeline as compression_pipeline
from mneme_core.compression.llm import AnthropicProvider
from mneme_core.vault.config import VaultConfig
from mneme_core.vault.file_lock import file_lock

from mneme_cc_plugin.hooks import stop

_P95_SAMPLE_COUNT = 100
_P95_WARMUP_COUNT = 5
_STOP_BUDGET_MS = 1000.0


@pytest.fixture
def vault(tmp_path: Path) -> VaultConfig:
    resolved = VaultConfig.from_path(tmp_path)
    resolved.state_dir.mkdir(parents=True, exist_ok=True)
    return resolved


def _capture(monkeypatch: pytest.MonkeyPatch) -> io.StringIO:
    output = io.StringIO()
    monkeypatch.setattr(sys, "stdout", output)
    return output


def _activate_full_profile(vault: VaultConfig) -> None:
    vault.kg_active_flag.write_text("on\n", encoding="utf-8")


def _stage_summary_record(vault: VaultConfig, session_id: str) -> None:
    now = datetime.now(UTC)
    day_dir = vault.staging_dir / "perf-host" / now.strftime("%Y-%m-%d")
    day_dir.mkdir(parents=True, exist_ok=True)
    record = {
        "session_id": session_id,
        "tool_name": "Edit",
        "tool_input": {"file_path": "notes/performance.md"},
        "captured_at": now.isoformat(),
    }
    (day_dir / "12-00-events.jsonl").write_text(
        json.dumps(record) + "\n",
        encoding="utf-8",
    )


def _nearest_rank_p95(samples_ms: list[float]) -> float:
    ordered = sorted(samples_ms)
    rank = math.ceil(0.95 * len(ordered))
    return ordered[rank - 1]


def test_stop_full_profile_makes_no_network_or_llm_call(
    monkeypatch: pytest.MonkeyPatch,
    vault: VaultConfig,
) -> None:
    calls: list[str] = []

    def _forbidden(name: str):
        def _raise(*_args: object, **_kwargs: object) -> NoReturn:
            calls.append(name)
            raise AssertionError(f"Stop attempted forbidden operation: {name}")

        return _raise

    monkeypatch.setattr(socket, "create_connection", _forbidden("socket.create_connection"))
    monkeypatch.setattr(socket.socket, "connect", _forbidden("socket.connect"))
    monkeypatch.setattr(
        http.client.HTTPConnection,
        "connect",
        _forbidden("http.client.HTTPConnection.connect"),
    )
    monkeypatch.setattr(urllib.request, "urlopen", _forbidden("urllib.request.urlopen"))
    monkeypatch.setattr(AnthropicProvider, "compress", _forbidden("AnthropicProvider.compress"))
    monkeypatch.setattr(
        compression_pipeline,
        "run_compression",
        _forbidden("compression.run_compression"),
    )

    _activate_full_profile(vault)
    _stage_summary_record(vault, "no-network")
    output = _capture(monkeypatch)
    stop.handle({"session_id": "no-network"}, vault)

    assert json.loads(output.getvalue())["continue"] is True
    assert calls == []
    assert vault.kg_community_refresh_flag.is_file()
    assert list((vault.root / "sessions").glob("*.md"))


def test_stop_full_handler_real_filesystem_p95_under_budget(
    monkeypatch: pytest.MonkeyPatch,
    vault: VaultConfig,
) -> None:
    """Measure 100 complete Stop calls after five untimed warmups."""
    session_id = "p95-full-handler"
    _activate_full_profile(vault)
    _stage_summary_record(vault, session_id)
    _capture(monkeypatch)

    for _ in range(_P95_WARMUP_COUNT):
        stop.handle({"session_id": session_id}, vault)

    samples_ms: list[float] = []
    for _ in range(_P95_SAMPLE_COUNT):
        started_ns = time.perf_counter_ns()
        stop.handle({"session_id": session_id}, vault)
        samples_ms.append((time.perf_counter_ns() - started_ns) / 1_000_000)

    p95_ms = _nearest_rank_p95(samples_ms)
    print(
        "STOP_P95_RESULT "
        f"samples={_P95_SAMPLE_COUNT} warmups={_P95_WARMUP_COUNT} "
        f"method=nearest-rank p95_ms={p95_ms:.3f} "
        f"min_ms={min(samples_ms):.3f} max_ms={max(samples_ms):.3f}",
        file=sys.stderr,
    )
    assert p95_ms < _STOP_BUDGET_MS


def test_stop_lock_contention_fails_soft_within_budget(
    monkeypatch: pytest.MonkeyPatch,
    vault: VaultConfig,
) -> None:
    fixed = datetime(2026, 7, 18, 12, 0, tzinfo=UTC)
    monkeypatch.setattr(stop, "_now_utc", lambda: fixed)
    lock_path = vault.state_dir / "locks" / "sessions-2026-07-18.lock"
    ready = threading.Event()
    release = threading.Event()
    holder_errors: list[BaseException] = []

    def _hold_lock() -> None:
        try:
            with file_lock(lock_path, timeout_s=1.0):
                ready.set()
                release.wait(timeout=2.0)
        except BaseException as exc:
            holder_errors.append(exc)
            ready.set()

    holder = threading.Thread(target=_hold_lock, daemon=True)
    holder.start()
    assert ready.wait(timeout=1.0)
    assert holder_errors == []

    output = _capture(monkeypatch)
    started = time.perf_counter()
    try:
        stop.handle({"session_id": "contended"}, vault)
    finally:
        release.set()
        holder.join(timeout=2.0)
    elapsed_ms = (time.perf_counter() - started) * 1000.0

    assert holder_errors == []
    assert not holder.is_alive()
    assert json.loads(output.getvalue())["continue"] is True
    assert elapsed_ms < _STOP_BUDGET_MS
    assert (vault.state_dir / "state.json").is_file()
    assert not (vault.root / "sessions" / "2026-07-18.md").exists()
