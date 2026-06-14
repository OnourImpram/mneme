"""Tests for the CCE config persistence (cce.config)."""

from __future__ import annotations

import json
from pathlib import Path

from mneme_core.cce.config import (
    DEFAULT_CONTEXT_THRESHOLD_PCT,
    DEFAULT_MAX_CHECKPOINTS,
    DEFAULT_REHYDRATION_TOKEN_BUDGET,
    DEFAULT_SALIENCE_WEIGHTS,
    SCHEMA_VERSION,
    CceConfig,
    read_config,
    write_config,
)


class TestDefaults:
    def test_default_is_disabled(self) -> None:
        cfg = CceConfig.default()
        assert cfg.enabled is False
        assert cfg.context_threshold_pct == DEFAULT_CONTEXT_THRESHOLD_PCT
        assert cfg.rehydration_token_budget == DEFAULT_REHYDRATION_TOKEN_BUDGET
        assert cfg.max_checkpoints == DEFAULT_MAX_CHECKPOINTS
        assert cfg.salience_weights == DEFAULT_SALIENCE_WEIGHTS
        assert cfg.schema_version == SCHEMA_VERSION

    def test_default_salience_weights_have_all_keys(self) -> None:
        cfg = CceConfig.default()
        for key in ("recent_edit", "fts5_hit", "recent_file", "decision", "todo"):
            assert key in cfg.salience_weights


class TestRoundTrip:
    def test_write_then_read_recovers_fields(self, tmp_path: Path) -> None:
        cfg = CceConfig(
            enabled=True,
            context_threshold_pct=0.80,
            rehydration_token_budget=2000,
            max_checkpoints=5,
            salience_weights={"recent_edit": 0.9, "todo": 0.5},
            schema_version=1,
        )
        path = tmp_path / "cce.json"
        write_config(path, cfg)
        loaded = read_config(path)
        assert loaded.enabled is True
        assert loaded.context_threshold_pct == 0.80
        assert loaded.rehydration_token_budget == 2000
        assert loaded.max_checkpoints == 5
        assert loaded.salience_weights == {"recent_edit": 0.9, "todo": 0.5}

    def test_missing_file_yields_defaults(self, tmp_path: Path) -> None:
        cfg = read_config(tmp_path / "missing.json")
        assert cfg.enabled is False
        assert cfg.context_threshold_pct == DEFAULT_CONTEXT_THRESHOLD_PCT
        assert cfg.salience_weights == DEFAULT_SALIENCE_WEIGHTS

    def test_malformed_json_yields_defaults(self, tmp_path: Path) -> None:
        path = tmp_path / "cce.json"
        path.write_text("{not-json", encoding="utf-8")
        assert read_config(path) == CceConfig.default()

    def test_partial_json_merges_with_defaults(self, tmp_path: Path) -> None:
        path = tmp_path / "cce.json"
        path.write_text(json.dumps({"enabled": True}), encoding="utf-8")
        cfg = read_config(path)
        assert cfg.enabled is True
        assert cfg.context_threshold_pct == DEFAULT_CONTEXT_THRESHOLD_PCT
        assert cfg.max_checkpoints == DEFAULT_MAX_CHECKPOINTS

    def test_corrupt_json_yields_defaults(self, tmp_path: Path) -> None:
        path = tmp_path / "cce.json"
        path.write_text("[1, 2, 3]", encoding="utf-8")
        assert read_config(path) == CceConfig.default()

    def test_wrong_type_field_yields_defaults(self, tmp_path: Path) -> None:
        path = tmp_path / "cce.json"
        path.write_text(
            json.dumps({"context_threshold_pct": "not-a-float"}),
            encoding="utf-8",
        )
        assert read_config(path) == CceConfig.default()

    def test_non_dict_salience_weights_yields_defaults(self, tmp_path: Path) -> None:
        path = tmp_path / "cce.json"
        path.write_text(
            json.dumps({"salience_weights": [1, 2, 3]}),
            encoding="utf-8",
        )
        cfg = read_config(path)
        assert cfg.salience_weights == DEFAULT_SALIENCE_WEIGHTS

    def test_write_creates_parent_dirs(self, tmp_path: Path) -> None:
        path = tmp_path / "nested" / "dir" / "cce.json"
        write_config(path, CceConfig.default())
        assert path.is_file()
