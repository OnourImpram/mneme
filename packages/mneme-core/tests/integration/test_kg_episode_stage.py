"""Tests for the PostToolUse KG episode stager."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from mneme_core.kg.episode_stage import (
    DEFAULT_CAPTURE_TOOLS,
    KgConfig,
    is_active,
    kg_config_from_vault,
    stage_event,
)
from mneme_core.vault.config import VaultConfig


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


class TestActiveGate:
    def test_no_op_without_flag(self, config: KgConfig) -> None:
        assert is_active(config) is False
        ok = stage_event({"tool_name": "Edit", "x": 1}, config)
        assert ok is False
        assert not config.queue_dir.exists()

    def test_writes_when_active(self, config: KgConfig) -> None:
        _activate(config)
        ok = stage_event({"tool_name": "Edit", "x": 1}, config)
        assert ok is True
        files = list(config.queue_dir.rglob("*-events.jsonl"))
        assert len(files) == 1


class TestRedaction:
    def test_private_content_replaced_with_short_hash(self, config: KgConfig) -> None:
        _activate(config)
        payload = {
            "tool_name": "Bash",
            "command": "echo <private>SUPER_SECRET_TOKEN</private>",
        }
        stage_event(payload, config)
        files = list(config.queue_dir.rglob("*-events.jsonl"))
        record = json.loads(files[0].read_text(encoding="utf-8").strip())
        cmd = record["payload"]["command"]
        assert "SUPER_SECRET_TOKEN" not in cmd
        assert "<REDACTED:" in cmd


class TestWhitelist:
    def test_filters_non_capture_tool(self, config: KgConfig) -> None:
        _activate(config)
        assert stage_event({"tool_name": "Read", "x": 1}, config) is False
        assert not list(config.queue_dir.rglob("*.jsonl"))

    def test_legacy_tool_key_accepted(self, config: KgConfig) -> None:
        _activate(config)
        assert stage_event({"tool": "Write", "path": "x.md"}, config) is True

    def test_default_capture_set_matches_post_tool_use_matcher(self) -> None:
        assert DEFAULT_CAPTURE_TOOLS == frozenset(
            {"Edit", "Write", "Bash", "Task", "MultiEdit"}
        )


class TestFailureSoft:
    def test_empty_event_is_false(self, config: KgConfig) -> None:
        _activate(config)
        assert stage_event({}, config) is False

    def test_non_dict_is_false(self, config: KgConfig) -> None:
        _activate(config)
        assert stage_event("not-a-dict", config) is False  # type: ignore[arg-type]
        assert stage_event(None, config) is False  # type: ignore[arg-type]

    def test_unwritable_queue_does_not_raise(
        self, config: KgConfig, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        _activate(config)

        def _boom(*_args: object, **_kwargs: object) -> None:
            raise OSError("disk full")

        monkeypatch.setattr(Path, "mkdir", _boom)
        assert stage_event({"tool_name": "Edit", "x": 1}, config) is False


class TestRecordShape:
    def test_record_contains_required_fields(self, config: KgConfig) -> None:
        _activate(config)
        stage_event({"tool_name": "Edit", "args": {"k": "v"}}, config)
        files = list(config.queue_dir.rglob("*-events.jsonl"))
        record = json.loads(files[0].read_text(encoding="utf-8").strip())
        for key in (
            "event_id",
            "ts",
            "host",
            "pid",
            "content_hash",
            "payload",
            "staged_by",
            "schema_version",
        ):
            assert key in record
        assert record["staged_by"] == "mneme.kg.episode_stage"
        assert record["schema_version"] == "1.0"
        assert record["host"] == "testhost"
        assert len(record["content_hash"]) == 64  # full sha256 hex

    def test_path_includes_host_and_date(self, config: KgConfig) -> None:
        _activate(config)
        stage_event({"tool_name": "Edit", "x": 1}, config)
        files = list(config.queue_dir.rglob("*-events.jsonl"))
        parts = files[0].parts
        assert "testhost" in parts
        # The dated directory is YYYY-MM-DD
        date_dir = files[0].parent.name
        assert len(date_dir) == 10
        assert date_dir[4] == "-" and date_dir[7] == "-"


class TestFactory:
    def test_kg_config_from_vault_resolves_all_paths(self, tmp_path: Path) -> None:
        vault = VaultConfig.from_path(tmp_path)
        cfg = kg_config_from_vault(vault, cost_cap_usd_monthly=10.0)
        assert cfg.queue_dir == vault.kg_queue_dir
        assert cfg.processed_dir == vault.kg_processed_dir
        assert cfg.active_flag == vault.kg_active_flag
        assert cfg.credentials_path == vault.kg_credentials_path
        assert cfg.community_refresh_flag == vault.kg_community_refresh_flag
        assert cfg.worker_log == vault.kg_worker_log
        assert cfg.cost_ledger == vault.kg_cost_ledger
        assert cfg.cost_cap_usd_monthly == 10.0
        assert cfg.capture_tools == DEFAULT_CAPTURE_TOOLS
