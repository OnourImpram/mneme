"""Tests for the SessionEnd KG community-refresh flag writer."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from mneme_core.kg.episode_stage import KgConfig
from mneme_core.kg.flush import mark_community_refresh


@pytest.fixture
def config(tmp_path: Path) -> KgConfig:
    state = tmp_path / "state"
    return KgConfig(
        queue_dir=state / "kg-queue",
        processed_dir=state / "kg-processed",
        active_flag=state / "kg-active.flag",
        credentials_path=state / "neo4j-credentials.json",
        community_refresh_flag=state / "kg-community-refresh.flag",
        worker_log=state / "telemetry" / "kg-worker.jsonl",
        cost_ledger=state / "cost-ledger.jsonl",
        host="testhost",
    )


def _activate(config: KgConfig) -> None:
    config.active_flag.parent.mkdir(parents=True, exist_ok=True)
    config.active_flag.write_text("on\n", encoding="utf-8")


class TestMarkCommunityRefresh:
    def test_no_op_without_active_flag(self, config: KgConfig) -> None:
        assert mark_community_refresh(config) is False
        assert not config.community_refresh_flag.exists()

    def test_writes_flag_when_active(self, config: KgConfig) -> None:
        _activate(config)
        assert mark_community_refresh(config) is True
        assert config.community_refresh_flag.is_file()
        record = json.loads(
            config.community_refresh_flag.read_text(encoding="utf-8").strip()
        )
        assert "requested_at" in record
        assert record["reason"] == "stop_hook"

    def test_custom_reason_is_persisted(self, config: KgConfig) -> None:
        _activate(config)
        mark_community_refresh(config, reason="session_end_explicit")
        record = json.loads(
            config.community_refresh_flag.read_text(encoding="utf-8").strip()
        )
        assert record["reason"] == "session_end_explicit"

    def test_swallows_exceptions(
        self, config: KgConfig, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _activate(config)

        def _boom(*_args: object, **_kwargs: object) -> None:
            raise OSError("disk full")

        monkeypatch.setattr(Path, "mkdir", _boom)
        # Must return False, not raise.
        assert mark_community_refresh(config) is False
