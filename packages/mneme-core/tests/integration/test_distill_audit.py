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
from mneme_core.distill.shell_compress import (
    COMPRESS_MIN_BYTES,
    DEFAULT_MAX_CHARS,
    compress_shell_output,
)
from mneme_core.vault.config import VaultConfig


@pytest.fixture
def vault(tmp_path: Path) -> VaultConfig:
    v = VaultConfig.from_path(tmp_path)
    (v.root / ".mneme").mkdir(parents=True, exist_ok=True)
    return v


def _verbose_shell_output() -> str:
    """Shell output carrying the redundancy shell_compress exists to remove.

    Every line is distinct, so the saving comes from stripping ANSI escapes
    and trailing whitespace rather than from folding repeats away entirely.
    That matters for the negative-control arm: the compressed form has to
    stay above the capture path's minimum size, or it would be skipped as
    too small and the control would prove nothing.

    Kept deliberately under ``DEFAULT_MAX_CHARS`` so no part of the measured
    reduction is max-chars truncation.
    """
    lines = [f"\x1b[32mPASS\x1b[0m test_case_{i:03d} ok   " for i in range(150)]
    return "\n".join(lines) + "\n"


def _write_staged_bash(vault: VaultConfig, stdout: str, name: str) -> Path:
    """Write one staging JSONL holding a single Bash record."""
    out_dir = vault.staging_dir / "testhost" / "2026-01-01"
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / name
    record = {
        "tool_name": "Bash",
        "tool_input": {"command": "pytest"},
        "tool_response": {"stdout": stdout, "interrupted": False},
    }
    path.write_text(json.dumps(record) + "\n", encoding="utf-8")
    return path


def _shell_advice(report: AuditReport) -> list[str]:
    return [r for r in report.recommendations if "shell_compress" in r]


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


class TestStagingCounts:
    """Live and archived are disjoint, and together they are the directory."""

    def test_counts_reconcile_with_the_filesystem(self, vault: VaultConfig) -> None:
        live_dir = vault.staging_dir / "testhost" / "2026-01-01"
        live_dir.mkdir(parents=True, exist_ok=True)
        archive_dir = vault.staging_dir / "archive" / "testhost" / "2025-12-01"
        archive_dir.mkdir(parents=True, exist_ok=True)
        for i in range(2):
            (live_dir / f"{i:02d}-00-events.jsonl").write_text(
                "x" * 100, encoding="utf-8"
            )
        for i in range(7):
            (archive_dir / f"{i:02d}-00-events.jsonl").write_text(
                "y" * 50, encoding="utf-8"
            )

        report = build_report(vault)

        # Measured straight off disk, then compared to what the audit claims.
        on_disk = list(vault.staging_dir.rglob("*.jsonl"))
        assert len(on_disk) == 9
        assert report.staging_files == 2
        assert report.staging_bytes == 200
        assert report.staging_archived_files == 7
        assert report.staging_archived_bytes == 350
        assert report.staging_total_files == len(on_disk)
        assert report.staging_total_bytes == sum(
            f.stat().st_size for f in on_disk
        )

    def test_live_and_archived_are_disjoint(self, vault: VaultConfig) -> None:
        report_before = build_report(vault)
        assert report_before.staging_total_files == 0
        _write_staged_bash(vault, "short", "00-00-events.jsonl")
        report = build_report(vault)
        assert report.staging_files == 1
        assert report.staging_archived_files == 0
        assert (
            report.staging_files + report.staging_archived_files
            == report.staging_total_files
        )


class TestShellCompressionAdvice:
    """The advice fires on measured headroom, and only on measured headroom."""

    def test_fires_when_staged_output_is_uncompressed(
        self, vault: VaultConfig
    ) -> None:
        raw = _verbose_shell_output()
        # Guard the fixture: below the truncation ceiling, so the reduction
        # reported here is real compression and not a chopped tail.
        assert len(raw) < DEFAULT_MAX_CHARS
        _write_staged_bash(vault, raw, "00-00-events.jsonl")
        report = build_report(vault)
        assert report.staging_sampled_files == 1
        assert report.shell_payload_bytes > 0
        advice = _shell_advice(report)
        assert advice, report.recommendations
        assert "Wire it into your PostToolUse capture." in advice[0]

    def test_silent_when_capture_already_compressed(
        self, vault: VaultConfig
    ) -> None:
        """Negative control for the arm above: same fixture, pre-compressed.

        This is the case the old unconditional text got wrong — it told
        operators to wire in compression their hook was already applying.
        """
        already = compress_shell_output(_verbose_shell_output()).compressed_text
        # Still large enough that the capture contract would compress it, so
        # silence below means "measured, no headroom" and not "skipped".
        assert len(already.encode("utf-8")) >= COMPRESS_MIN_BYTES
        _write_staged_bash(vault, already, "00-00-events.jsonl")
        report = build_report(vault)
        assert report.staging_sampled_files == 1
        assert report.shell_payload_bytes > 0  # the payload was measured
        assert _shell_advice(report) == []  # and found to have no headroom

    def test_silent_when_nothing_was_sampled(self, vault: VaultConfig) -> None:
        # A large backlog of non-Bash records: no shell output to judge.
        out_dir = vault.staging_dir / "testhost" / "2026-01-01"
        out_dir.mkdir(parents=True, exist_ok=True)
        record = {"tool_name": "Edit", "tool_response": {"filePath": "a.md"}}
        (out_dir / "00-00-events.jsonl").write_text(
            (json.dumps(record) + "\n") * 2000, encoding="utf-8"
        )
        report = build_report(vault)
        assert report.staging_bytes > 100_000  # old gate would have fired here
        assert report.shell_payload_bytes == 0
        assert _shell_advice(report) == []

    def test_below_threshold_output_is_not_counted(
        self, vault: VaultConfig
    ) -> None:
        """Mirrors the capture contract: tiny outputs are not compressed."""
        _write_staged_bash(vault, "ok\n", "00-00-events.jsonl")
        report = build_report(vault)
        assert report.shell_payload_bytes == 0
        assert _shell_advice(report) == []


class TestRecommendations:
    def test_no_data_yields_baseline_message(self, vault: VaultConfig) -> None:
        report = build_report(vault)
        joined = " ".join(report.recommendations).lower()
        # Either the "no data" or "enable injection_dedup" recommendation,
        # both are valid baseline outputs.
        assert "next week" in joined or "injection_dedup" in joined
