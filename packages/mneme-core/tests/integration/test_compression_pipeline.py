"""End-to-end tests for the Phase F compression pipeline.

A fake ``LlmProvider`` is injected so the pipeline can be exercised
end-to-end without an Anthropic key.
"""

from __future__ import annotations

import dataclasses
import json
from pathlib import Path

import pytest

from mneme_core.compression.config import CompressionConfig
from mneme_core.compression.llm import (
    CompressionResult,
    LlmCallSpec,
    LlmProvider,
    LlmProviderError,
)
from mneme_core.compression.pipeline import (
    PipelineConfig,
    RunReport,
    _extract_content_hashes,
    load_prompt,
    pipeline_config_from_vault,
    run_compression,
)
from mneme_core.compression.staging import StagingConfig, capture_event
from mneme_core.vault.config import VaultConfig


class FakeProvider:
    """Deterministic LLM stand-in for pipeline tests."""

    def __init__(
        self,
        *,
        text: str = "",
        raise_provider_error: bool = False,
        raise_runtime: bool = False,
        tokens_in: int = 100,
        tokens_out: int = 50,
    ) -> None:
        self.text = text
        self.raise_provider_error = raise_provider_error
        self.raise_runtime = raise_runtime
        self.tokens_in = tokens_in
        self.tokens_out = tokens_out
        self.last_spec: LlmCallSpec | None = None

    def compress(self, spec: LlmCallSpec) -> CompressionResult:
        self.last_spec = spec
        if self.raise_provider_error:
            raise LlmProviderError("simulated provider error")
        if self.raise_runtime:
            raise RuntimeError("simulated unexpected error")
        return CompressionResult(
            text=self.text,
            tokens_in=self.tokens_in,
            tokens_out=self.tokens_out,
            model=spec.model,
        )


def _make_observation(date_iso: str, hash_hex: str = "deadbeefdeadbeef") -> str:
    """Construct a compression-pipeline observation block matching the
    canonical `compressed` 9-type schema from `docs/VAULT.md`.

    Phase J Day 6 audit aligned this with VAULT.md spec: type must be
    one of the nine canonical literals (`compressed`), schema_version
    is an integer, and the required extras for compression output are
    `source_session_id` and `compression_score`.
    """
    return (
        "---\n"
        f"id: {date_iso}-compressed-{hash_hex[:8]}\n"
        "type: compressed\n"
        f"created: {date_iso}T00:00:00+00:00\n"
        "schema_version: 1\n"
        'source_session_id: "s-test"\n'
        "compression_score: 0.85\n"
        f'content_hash: "{hash_hex}"\n'
        "tags: []\n"
        "confidentiality: internal\n"
        "---\n\n"
        "## Test observation\n\n"
        "Body paragraph.\n"
    )


@pytest.fixture
def vault(tmp_path: Path) -> VaultConfig:
    v = VaultConfig.from_path(tmp_path)
    (v.root / ".mneme").mkdir(parents=True, exist_ok=True)
    return v


@pytest.fixture
def config(vault: VaultConfig) -> PipelineConfig:
    enabled_cfg = CompressionConfig(enabled=True, cost_cap_usd_monthly=10.0)
    cfg = pipeline_config_from_vault(vault, enabled_cfg)
    # Phase J Codex fix: production cutoff skips files <60s old to avoid
    # the load-then-archive race with PostToolUse. Tests seed events
    # synchronously, so disable the cutoff for deterministic runs.
    return dataclasses.replace(cfg, staging_active_window_s=0.0)


def _seed_staging(vault: VaultConfig, count: int = 1) -> None:
    cfg = StagingConfig(
        staging_dir=vault.staging_dir,
        audit_dir=vault.audit_log_dir,
        host=pipeline_config_from_vault(vault, CompressionConfig.default()).host,
    )
    for i in range(count):
        capture_event(
            {"tool_name": "Edit", "tool_input": {"i": i}, "tool_response": {"ok": True}},
            cfg,
        )


class TestGating:
    def test_disabled_returns_disabled_status(self, vault: VaultConfig) -> None:
        cfg = pipeline_config_from_vault(vault, CompressionConfig(enabled=False))
        report = run_compression(cfg, FakeProvider(text=_make_observation("2026-06-15")))
        assert report.status == "disabled"

    def test_pause_flag_short_circuits(
        self, config: PipelineConfig, vault: VaultConfig
    ) -> None:
        vault.compression_pause_flag.parent.mkdir(parents=True, exist_ok=True)
        vault.compression_pause_flag.write_text("paused", encoding="utf-8")
        report = run_compression(config, FakeProvider(text="x"))
        assert report.status == "paused"


class TestEmptyStaging:
    def test_returns_empty_when_no_events(self, config: PipelineConfig) -> None:
        report = run_compression(config, FakeProvider())
        assert report.status == "empty"


class TestDryRun:
    def test_loads_events_without_calling_provider(
        self, config: PipelineConfig, vault: VaultConfig
    ) -> None:
        _seed_staging(vault, count=3)
        provider = FakeProvider(raise_runtime=True)  # would crash if called
        report = run_compression(config, provider, dry_run=True)
        assert report.status == "dry-run"
        assert report.events_loaded == 3
        assert provider.last_spec is None


class TestSuccessPath:
    def test_writes_observation_and_ledger(
        self, config: PipelineConfig, vault: VaultConfig
    ) -> None:
        _seed_staging(vault, count=2)
        markdown = _make_observation(date_iso="2026-06-15", hash_hex="abc123abc123abc1")
        report = run_compression(config, FakeProvider(text=markdown))
        assert report.status == "ok", report.reason
        assert report.events_loaded == 2
        assert report.tokens_in == 100
        assert report.tokens_out == 50
        assert report.cost_usd > 0
        # Daily log written.
        assert Path(report.written_path or "").is_file()
        # Cost ledger has one new line.
        assert vault.kg_cost_ledger.is_file()
        line = vault.kg_cost_ledger.read_text(encoding="utf-8").strip().splitlines()[-1]
        rec = json.loads(line)
        assert rec["kind"] == "compression"
        assert rec["tokens_in"] == 100

    def test_archives_processed_staging_files(
        self, config: PipelineConfig, vault: VaultConfig
    ) -> None:
        _seed_staging(vault, count=1)
        markdown = _make_observation(date_iso="2026-06-15", hash_hex="0011223344556677")
        run_compression(config, FakeProvider(text=markdown))
        # No live files remain outside archive/
        live = [
            p for p in vault.staging_dir.rglob("*.jsonl") if "archive" not in p.parts
        ]
        archived = list((vault.staging_dir / "archive").rglob("*.jsonl"))
        assert live == []
        assert len(archived) >= 1


class TestDuplicateDetection:
    def test_skips_when_content_hashes_already_present(
        self, config: PipelineConfig, vault: VaultConfig
    ) -> None:
        _seed_staging(vault, count=1)
        # First run, hash X, writes file.
        first = _make_observation("2026-06-15", hash_hex="bbbbccccddddeeee")
        run_compression(config, FakeProvider(text=first))
        # Re-seed staging then run again with the SAME hash. Should
        # short-circuit on duplicate detection.
        _seed_staging(vault, count=1)
        second = _make_observation("2026-06-15", hash_hex="bbbbccccddddeeee")
        report = run_compression(config, FakeProvider(text=second))
        assert report.status == "duplicate"


class TestEmptyOutput:
    def test_blank_text_returns_empty_output(
        self, config: PipelineConfig, vault: VaultConfig
    ) -> None:
        _seed_staging(vault, count=1)
        report = run_compression(config, FakeProvider(text="   \n  "))
        assert report.status == "empty_output"


class TestProviderErrors:
    def test_provider_error_reports_stable_envelope(
        self, config: PipelineConfig, vault: VaultConfig
    ) -> None:
        _seed_staging(vault, count=1)
        report = run_compression(config, FakeProvider(raise_provider_error=True))
        assert report.status == "provider_error"
        assert "simulated provider error" in (report.reason or "")

    def test_unexpected_runtime_does_not_raise_out(
        self, config: PipelineConfig, vault: VaultConfig
    ) -> None:
        _seed_staging(vault, count=1)
        report = run_compression(config, FakeProvider(raise_runtime=True))
        assert report.status == "error"
        assert "provider_unexpected" in (report.reason or "")


class TestCostCap:
    def test_breach_returns_cost_cap_status(
        self, vault: VaultConfig
    ) -> None:
        # Pre-stamp the ledger with month-to-date spend above the cap.
        vault.kg_cost_ledger.parent.mkdir(parents=True, exist_ok=True)
        from datetime import datetime, timezone

        cur = datetime.now(timezone.utc).strftime("%Y-%m")
        rec = {
            "ts": f"{cur}-01T00:00:00+00:00",
            "cost_usd": 50.0,
            "kind": "compression",
        }
        with vault.kg_cost_ledger.open("a", encoding="utf-8") as fp:
            fp.write(json.dumps(rec) + "\n")

        _seed_staging(vault, count=1)
        cfg = dataclasses.replace(
            pipeline_config_from_vault(
                vault, CompressionConfig(enabled=True, cost_cap_usd_monthly=10.0)
            ),
            staging_active_window_s=0.0,
        )
        report = run_compression(cfg, FakeProvider(text=_make_observation("2026-06-15")))
        assert report.status == "cost_cap_exceeded"
        assert vault.compression_pause_flag.is_file()


class TestPromptResource:
    def test_load_prompt_returns_bundled_resource(self) -> None:
        text = load_prompt(None)
        assert "Four-dimensional quality rubric" in text
        assert "Accuracy" in text


class TestHashExtraction:
    def test_pulls_first_16_hex_chars(self) -> None:
        md = _make_observation("2026-06-15", hash_hex="0123456789abcdef")
        hashes = _extract_content_hashes(md)
        assert hashes == {"0123456789abcdef"}
