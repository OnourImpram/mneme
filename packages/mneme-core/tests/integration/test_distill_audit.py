"""Integration tests for the ``mneme-audit`` CLI report builder."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from mneme_core.distill.audit import AuditReport, build_report, cli
from mneme_core.distill.injection_dedup import (
    InjectionTracker,
    mark_injected,
    save_tracker,
)
from mneme_core.vault.config import VaultConfig


@pytest.fixture
def vault(tmp_path: Path) -> VaultConfig:
    v = VaultConfig.from_path(tmp_path)
    (v.root / ".mneme").mkdir(parents=True, exist_ok=True)
    return v


class TestEmptyVault:
    def test_report_handles_zero_data(self, vault: VaultConfig) -> None:
        report = build_report(vault)
        assert isinstance(report, AuditReport)
        assert report.telemetry_records == 0
        assert report.tokens_in_total == 0
        assert report.staging_files == 0
        assert report.injection_tracker_sessions == 0
        assert report.recommendations  # at least one recommendation


class TestTelemetryAggregation:
    def test_sums_tokens_and_cost(self, vault: VaultConfig) -> None:
        vault.telemetry_dir.mkdir(parents=True, exist_ok=True)
        f = vault.telemetry_dir / "session.jsonl"
        records = [
            {"tokens_in": 100, "tokens_out": 50, "cost_usd": 0.001},
            {"tokens_in": 200, "tokens_out": 80, "cost_usd": 0.002},
            {"tokens_in": 400, "tokens_out": 200, "cost_usd": 0.005},
        ]
        with f.open("w", encoding="utf-8") as fp:
            for r in records:
                fp.write(json.dumps(r) + "\n")
        report = build_report(vault)
        assert report.telemetry_records == 3
        assert report.tokens_in_total == 700
        assert report.tokens_out_total == 330
        assert report.cost_usd_total == pytest.approx(0.008)
        assert report.median_tokens_in == 200.0

    def test_skips_malformed_lines(self, vault: VaultConfig) -> None:
        vault.telemetry_dir.mkdir(parents=True, exist_ok=True)
        f = vault.telemetry_dir / "session.jsonl"
        f.write_text(
            "\n".join(
                [
                    json.dumps({"tokens_in": 10}),
                    "not-json",
                    "",
                    json.dumps({"tokens_in": 20}),
                ]
            ),
            encoding="utf-8",
        )
        report = build_report(vault)
        assert report.telemetry_records == 2
        assert report.tokens_in_total == 30


class TestInjectionTrackerStats:
    def test_counts_sessions_and_hits(self, vault: VaultConfig) -> None:
        # Seed two tracker files.
        for sid, hits, skips in [("s-1", 5, 1), ("s-2", 3, 0)]:
            t = InjectionTracker(session_id=sid)
            for i in range(hits):
                mark_injected(t, f"{sid}-{i}")
            t.skips = skips
            save_tracker(vault.state_dir, t)

        report = build_report(vault)
        assert report.injection_tracker_sessions == 2
        assert report.injection_hits_total == 8
        assert report.injection_skips_total == 1


class TestCliEntry:
    def test_cli_emits_json(self, vault: VaultConfig) -> None:
        runner = CliRunner()
        result = runner.invoke(cli, ["--vault", str(vault.root)])
        assert result.exit_code == 0, result.output
        payload = json.loads(result.output)
        assert "vault_root" in payload
        assert "recommendations" in payload


class TestRecommendations:
    def test_no_data_yields_baseline_message(self, vault: VaultConfig) -> None:
        report = build_report(vault)
        joined = " ".join(report.recommendations).lower()
        # Either the "no data" or "enable injection_dedup" recommendation,
        # both are valid baseline outputs.
        assert "next week" in joined or "injection_dedup" in joined
