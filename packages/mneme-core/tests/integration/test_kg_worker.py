"""Tests for the out-of-band KG drain worker."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from mneme_core.compression.ledger import month_to_date_spend
from mneme_core.kg.episode_stage import KgConfig, stage_event
from mneme_core.kg.worker import (
    community_refresh,
    drain_dry_run,
    drain_live,
)


@pytest.fixture
def config(tmp_path: Path) -> KgConfig:
    state = tmp_path / "state"
    cfg = KgConfig(
        queue_dir=state / "kg-queue",
        processed_dir=state / "kg-processed",
        active_flag=state / "kg-active.flag",
        credentials_path=state / "neo4j-credentials.json",
        community_refresh_flag=state / "kg-community-refresh.flag",
        worker_log=state / "telemetry" / "kg-worker.jsonl",
        cost_ledger=state / "cost-ledger.jsonl",
        host="testhost",
    )
    # Activate so stage_event will write fixtures.
    cfg.active_flag.parent.mkdir(parents=True, exist_ok=True)
    cfg.active_flag.write_text("on\n", encoding="utf-8")
    return cfg


def _seed_queue(config: KgConfig, count: int) -> None:
    for i in range(count):
        stage_event({"tool_name": "Edit", "args": {"i": i}}, config)


class TestDryRun:
    def test_empty_queue_returns_zero(self, config: KgConfig) -> None:
        result = drain_dry_run(config)
        assert result["mode"] == "dry-run"
        assert result["files_found"] == 0
        assert result["total_events"] == 0
        assert result["unique_events_by_hash"] == 0

    def test_counts_match_seeded_events(self, config: KgConfig) -> None:
        _seed_queue(config, 5)
        result = drain_dry_run(config)
        assert result["total_events"] == 5
        assert result["unique_events_by_hash"] == 5
        assert result["files_found"] >= 1

    def test_writes_worker_log_entry(self, config: KgConfig) -> None:
        drain_dry_run(config)
        assert config.worker_log.is_file()
        line = config.worker_log.read_text(encoding="utf-8").strip().splitlines()[-1]
        record = json.loads(line)
        assert record["mode"] == "dry-run"


class TestDrainLive:
    def test_returns_error_when_graphiti_not_installed(
        self, config: KgConfig, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Simulate ImportError by hiding graphiti_core from sys.modules
        # via a path that triggers the worker's ``except ImportError``.
        import sys

        monkeypatch.setitem(sys.modules, "graphiti_core", None)
        result = drain_live(config)
        assert "error" in result
        assert "graphiti_core" in result["error"]

    def test_returns_error_when_credentials_missing(
        self, config: KgConfig, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Force the import to succeed by injecting stub modules.
        import sys
        import types

        stub_core = types.ModuleType("graphiti_core")
        stub_core.Graphiti = lambda *a, **k: None  # type: ignore[attr-defined]
        stub_nodes = types.ModuleType("graphiti_core.nodes")
        stub_nodes.EpisodeType = types.SimpleNamespace(text="text")  # type: ignore[attr-defined]
        monkeypatch.setitem(sys.modules, "graphiti_core", stub_core)
        monkeypatch.setitem(sys.modules, "graphiti_core.nodes", stub_nodes)
        result = drain_live(config)
        assert "error" in result
        assert "credentials" in result["error"].lower()


class TestCommunityRefresh:
    def test_returns_error_when_graphiti_not_installed(
        self, config: KgConfig, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import sys

        monkeypatch.setitem(sys.modules, "graphiti_core", None)
        result = community_refresh(config)
        assert "error" in result


def _install_graphiti_stub(
    monkeypatch: pytest.MonkeyPatch, *, add_episode_raises: bool = False
) -> None:
    """Inject a minimal in-process Graphiti so drain_live runs offline."""
    import sys
    import types

    class _StubGraphiti:
        def __init__(self, *_a: object, **_k: object) -> None:
            pass

        async def add_episode(self, **_kwargs: object) -> None:
            if add_episode_raises:
                raise RuntimeError("provider boom")

    stub_core = types.ModuleType("graphiti_core")
    stub_core.Graphiti = _StubGraphiti  # type: ignore[attr-defined]
    stub_nodes = types.ModuleType("graphiti_core.nodes")
    stub_nodes.EpisodeType = types.SimpleNamespace(text="text")  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "graphiti_core", stub_core)
    monkeypatch.setitem(sys.modules, "graphiti_core.nodes", stub_nodes)


def _write_credentials(config: KgConfig) -> None:
    config.credentials_path.parent.mkdir(parents=True, exist_ok=True)
    config.credentials_path.write_text(
        json.dumps({"bolt_url": "bolt://x", "user": "neo4j", "password": "x"}),
        encoding="utf-8",
    )


class TestCostCap:
    """Reservation-backed cap enforcement (Codex-class check-then-act race)."""

    def test_cap_blocks_episodes_and_never_overspends(
        self, config: KgConfig, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        config.cost_cap_usd_monthly = 0.005  # room for exactly two 0.002 adds
        _seed_queue(config, 5)
        _write_credentials(config)
        _install_graphiti_stub(monkeypatch)

        result = drain_live(config, per_episode_usd_estimate=0.002)

        assert result["episodes_added"] == 2
        assert result["skipped_by_cost_cap"] == 3
        assert result["episodes_failed"] == 0
        spend = month_to_date_spend(config.cost_ledger, kind="kg_add_episode")
        assert spend <= config.cost_cap_usd_monthly
        assert spend == pytest.approx(0.004)

    def test_success_settles_reservation_to_actual_cost(
        self, config: KgConfig, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        config.cost_cap_usd_monthly = 1.0
        _seed_queue(config, 3)
        _write_credentials(config)
        _install_graphiti_stub(monkeypatch)

        result = drain_live(config, per_episode_usd_estimate=0.01)

        assert result["episodes_added"] == 3
        lines = [
            json.loads(ln)
            for ln in config.cost_ledger.read_text(encoding="utf-8")
            .strip()
            .splitlines()
        ]
        reserved = [r for r in lines if r.get("kind") == "kg_add_episode-reserved"]
        settled = [r for r in lines if r.get("kind") == "kg_add_episode"]
        assert reserved == []  # every reservation was settled, none linger
        assert len(settled) == 3
        spend = month_to_date_spend(config.cost_ledger, kind="kg_add_episode")
        assert spend == pytest.approx(0.03)

    def test_provider_failure_rolls_back_reservation(
        self, config: KgConfig, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        config.cost_cap_usd_monthly = 1.0
        _seed_queue(config, 2)
        _write_credentials(config)
        _install_graphiti_stub(monkeypatch, add_episode_raises=True)

        result = drain_live(config, per_episode_usd_estimate=0.01)

        assert result["episodes_added"] == 0
        assert result["episodes_failed"] == 2
        spend = month_to_date_spend(config.cost_ledger, kind="kg_add_episode")
        assert spend == pytest.approx(0.0)

    def test_uncapped_run_still_records_spend(
        self, config: KgConfig, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        config.cost_cap_usd_monthly = None
        _seed_queue(config, 2)
        _write_credentials(config)
        _install_graphiti_stub(monkeypatch)

        result = drain_live(config, per_episode_usd_estimate=0.01)

        assert result["episodes_added"] == 2
        spend = month_to_date_spend(config.cost_ledger, kind="kg_add_episode")
        assert spend == pytest.approx(0.02)
