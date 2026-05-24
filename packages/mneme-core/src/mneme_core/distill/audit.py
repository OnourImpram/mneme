"""``mneme audit`` CLI: session token-consumption report.

Reads the telemetry JSONL written by ``mneme_core.telemetry.writer``
and the staging directory, then reports:

* Tokens-per-tool-call distribution (count + median + p95).
* Total compressed-shell-output savings (bytes saved by
  ``distill.shell_compress`` based on a heuristic estimate).
* Injection-dedup hit rate (from any present trackers under
  ``vault/.mneme/injection-tracker/``).
* Top recommendations (actionable, with concrete next steps).

The CLI is read-only. It does not mutate the vault or the indexes,
and it never spawns an LLM call. Operators run it manually or via a
cron sweep to keep an eye on hidden token waste.
"""

from __future__ import annotations

import json
import statistics
import sys
from dataclasses import dataclass, field
from pathlib import Path

import click

from ..vault.config import VaultConfig, VaultNotFoundError
from .injection_dedup import TRACKER_SUBDIR
from .shell_compress import compress_shell_output


@dataclass
class AuditReport:
    """Structured output. Serialized to JSON for the CLI surface."""

    vault_root: str
    telemetry_records: int = 0
    tokens_in_total: int = 0
    tokens_out_total: int = 0
    cost_usd_total: float = 0.0
    median_tokens_in: float = 0.0
    p95_tokens_in: float = 0.0
    staging_files: int = 0
    staging_bytes: int = 0
    staging_estimated_compressed_bytes: int = 0
    injection_tracker_sessions: int = 0
    injection_hits_total: int = 0
    injection_skips_total: int = 0
    recommendations: list[str] = field(default_factory=list)


def _resolve(explicit: Path | None) -> VaultConfig:
    if explicit is not None:
        return VaultConfig.from_path(explicit)
    return VaultConfig.resolve()


def _read_telemetry(vault: VaultConfig) -> list[dict]:
    out: list[dict] = []
    if not vault.telemetry_dir.exists():
        return out
    for jsonl in vault.telemetry_dir.rglob("*.jsonl"):
        try:
            with jsonl.open("r", encoding="utf-8") as fp:
                for line in fp:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        rec = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if isinstance(rec, dict):
                        out.append(rec)
        except OSError:
            continue
    return out


def _percentile(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    sorted_vals = sorted(values)
    if p <= 0:
        return sorted_vals[0]
    if p >= 1:
        return sorted_vals[-1]
    index = int(round((len(sorted_vals) - 1) * p))
    return sorted_vals[index]


def _staging_stats(vault: VaultConfig) -> tuple[int, int, int]:
    """Return (file_count, total_bytes, estimated_compressed_bytes)."""
    if not vault.staging_dir.exists():
        return (0, 0, 0)
    files = [
        f
        for f in vault.staging_dir.rglob("*.jsonl")
        if "archive" not in f.parts
    ]
    total = 0
    sample_estimate = 0
    sample_taken = 0
    for f in files:
        try:
            stat = f.stat()
        except OSError:
            continue
        total += stat.st_size
        if sample_taken < 5:
            try:
                text = f.read_text(encoding="utf-8")
            except OSError:
                continue
            stats = compress_shell_output(text)
            sample_estimate += stats.compressed_bytes
            sample_taken += 1
    estimated_total = (
        int(total * (sample_estimate / max(1, sum(
            f.stat().st_size for f in files[:5] if f.exists()
        ))))
        if sample_taken > 0 and total > 0
        else total
    )
    return (len(files), total, estimated_total)


def _injection_tracker_stats(vault: VaultConfig) -> tuple[int, int, int]:
    """Return (session_count, hits_total, skips_total)."""
    tracker_dir = vault.state_dir / TRACKER_SUBDIR
    if not tracker_dir.is_dir():
        return (0, 0, 0)
    sessions = 0
    hits = 0
    skips = 0
    for f in tracker_dir.glob("*.json"):
        try:
            raw = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(raw, dict):
            continue
        sessions += 1
        hits += int(raw.get("hits", 0) or 0)
        skips += int(raw.get("skips", 0) or 0)
    return (sessions, hits, skips)


def _build_recommendations(report: AuditReport) -> list[str]:
    out: list[str] = []
    if report.staging_bytes > 100_000:
        savings = report.staging_bytes - report.staging_estimated_compressed_bytes
        ratio = (
            report.staging_estimated_compressed_bytes / report.staging_bytes
            if report.staging_bytes
            else 1.0
        )
        out.append(
            f"distill.shell_compress projects ~{savings // 1024} KB savings "
            f"({(1 - ratio) * 100:.0f}% reduction) against the current staging "
            "backlog. Wire it into your PostToolUse capture."
        )
    if report.injection_tracker_sessions == 0:
        out.append(
            "No injection-tracker state present. Enable distill.injection_dedup "
            "in your SessionStart hook to skip re-injecting the same memories "
            "within a session."
        )
    elif report.injection_skips_total > report.injection_hits_total / 4:
        out.append(
            f"Injection dedup saved {report.injection_skips_total} "
            "duplicate re-injections so far. Keep it on."
        )
    if report.cost_usd_total > 5.0:
        out.append(
            f"Telemetry shows ${report.cost_usd_total:.2f} cumulative LLM "
            "spend. Consider lowering compression cost cap or switching "
            "to a smaller model for routine sessions."
        )
    if not out:
        out.append(
            "No major waste patterns detected. Run 'mneme audit' again next "
            "week to keep an eye on drift."
        )
    return out


def build_report(vault: VaultConfig) -> AuditReport:
    """Pure function. The CLI is a thin Click wrapper around this."""
    records = _read_telemetry(vault)
    tokens_in_vals = [
        float(r.get("tokens_in", 0))
        for r in records
        if isinstance(r.get("tokens_in"), (int, float))
    ]
    report = AuditReport(vault_root=str(vault.root))
    report.telemetry_records = len(records)
    report.tokens_in_total = int(sum(tokens_in_vals))
    report.tokens_out_total = int(
        sum(float(r.get("tokens_out", 0)) for r in records)
    )
    report.cost_usd_total = round(
        sum(
            float(r.get("cost_usd", r.get("usd", 0)) or 0)
            for r in records
        ),
        4,
    )
    if tokens_in_vals:
        report.median_tokens_in = float(statistics.median(tokens_in_vals))
        report.p95_tokens_in = _percentile(tokens_in_vals, 0.95)
    file_count, total_bytes, est_bytes = _staging_stats(vault)
    report.staging_files = file_count
    report.staging_bytes = total_bytes
    report.staging_estimated_compressed_bytes = est_bytes
    sessions, hits, skips = _injection_tracker_stats(vault)
    report.injection_tracker_sessions = sessions
    report.injection_hits_total = hits
    report.injection_skips_total = skips
    report.recommendations = _build_recommendations(report)
    return report


@click.command(help="Per-session token consumption report.")
@click.option(
    "--vault",
    "vault_root",
    type=click.Path(file_okay=False, path_type=Path),
    default=None,
)
def cli(vault_root: Path | None) -> None:
    try:
        vault = _resolve(vault_root)
    except VaultNotFoundError as exc:
        raise click.ClickException(str(exc)) from exc
    report = build_report(vault)
    click.echo(json.dumps(report.__dict__, indent=2, default=str))


def main() -> None:
    cli(prog_name="mneme-audit")


if __name__ == "__main__":  # pragma: no cover
    main()
    sys.exit(0)
