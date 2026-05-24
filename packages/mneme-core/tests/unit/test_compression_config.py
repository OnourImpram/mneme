"""Tests for the Phase F compression config persistence."""

from __future__ import annotations

import json
from pathlib import Path

from mneme_core.compression.config import (
    DEFAULT_COST_CAP_USD_MONTHLY,
    SCHEMA_VERSION,
    CompressionConfig,
    ensure_default_config,
    read_config,
    write_config,
)
from mneme_core.compression.llm import DEFAULT_MODEL


class TestDefaults:
    def test_default_is_disabled(self) -> None:
        cfg = CompressionConfig.default()
        assert cfg.enabled is False
        assert cfg.cost_cap_usd_monthly == DEFAULT_COST_CAP_USD_MONTHLY
        assert cfg.model == DEFAULT_MODEL
        assert cfg.schema_version == SCHEMA_VERSION


class TestRoundTrip:
    def test_write_then_read_recovers_fields(self, tmp_path: Path) -> None:
        cfg = CompressionConfig(
            enabled=True,
            cost_cap_usd_monthly=10.0,
            model="claude-opus-4-7",
            max_tokens=8000,
            max_payload_bytes=50_000,
        )
        path = tmp_path / "compression.json"
        write_config(path, cfg)
        loaded = read_config(path)
        assert loaded == cfg

    def test_missing_file_yields_defaults(self, tmp_path: Path) -> None:
        cfg = read_config(tmp_path / "missing.json")
        assert cfg.enabled is False
        assert cfg.cost_cap_usd_monthly == DEFAULT_COST_CAP_USD_MONTHLY

    def test_malformed_json_yields_defaults(self, tmp_path: Path) -> None:
        path = tmp_path / "compression.json"
        path.write_text("{not-json", encoding="utf-8")
        assert read_config(path) == CompressionConfig.default()

    def test_partial_json_merges_with_defaults(self, tmp_path: Path) -> None:
        path = tmp_path / "compression.json"
        path.write_text(json.dumps({"enabled": True}), encoding="utf-8")
        cfg = read_config(path)
        assert cfg.enabled is True
        assert cfg.cost_cap_usd_monthly == DEFAULT_COST_CAP_USD_MONTHLY

    def test_non_object_root_yields_defaults(self, tmp_path: Path) -> None:
        path = tmp_path / "compression.json"
        path.write_text(json.dumps([1, 2, 3]), encoding="utf-8")
        assert read_config(path) == CompressionConfig.default()

    def test_wrong_type_field_yields_defaults(self, tmp_path: Path) -> None:
        path = tmp_path / "compression.json"
        path.write_text(
            json.dumps({"cost_cap_usd_monthly": "not-a-number"}),
            encoding="utf-8",
        )
        assert read_config(path) == CompressionConfig.default()


class TestEnsureDefault:
    def test_creates_file_when_missing(self, tmp_path: Path) -> None:
        path = tmp_path / "compression.json"
        cfg = ensure_default_config(path)
        assert path.is_file()
        assert cfg == CompressionConfig.default()

    def test_idempotent_when_present(self, tmp_path: Path) -> None:
        path = tmp_path / "compression.json"
        first = ensure_default_config(path)
        # Pre-modify the file to verify the second call does not clobber.
        existing = CompressionConfig(
            enabled=True, cost_cap_usd_monthly=99.0
        )
        write_config(path, existing)
        second = ensure_default_config(path)
        assert second.enabled is True
        assert second.cost_cap_usd_monthly == 99.0
        assert first.enabled is False  # untouched local copy
