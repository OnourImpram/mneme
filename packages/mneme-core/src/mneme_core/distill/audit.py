"""``mneme-audit`` CLI: session token-consumption report.

Reads the telemetry JSONL written by ``mneme_core.telemetry.writer``
and the staging directory, then reports:

* Tokens-per-tool-call distribution (count + median + p95).
* Staging volume, split into the live backlog and the archive so the two
  add up to what the directory actually holds.
* Remaining compressed-shell-output headroom, sampled from the newest
  staged records and measured on the payloads the capture path compresses.
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
from typing import Any

import click

from ..vault.config import VaultConfig, VaultNotFoundError
from .injection_dedup import TRACKER_SUBDIR
from .shell_compress import compress_shell_output, iter_compressible_outputs

# How many of the newest live staging files to open when measuring shell
# compression headroom. Small on purpose: the CLI is run interactively.
_SAMPLE_FILES = 5

# Minimum recoverable fraction of sampled shell-output bytes before the
# report says anything about compression. Below this the backlog is already
# near-minimal and the only honest advice is none.
_SHELL_HEADROOM_FLOOR = 0.10


@dataclass
class AuditReport:
    """Structured output. Serialized to JSON for the CLI surface.

    ``staging_files`` and ``staging_bytes`` count the *live* backlog only:
    the JSONL the size cap governs and the compression pipeline still has
    to drain. Files that ``enforce_size_cap`` already rolled into
    ``staging/archive/`` are excluded — they are still on disk but no
    longer in play. On a busy vault the archive dominates, so those two
    numbers land several times below what a directory listing shows. That
    is the intended scope, not an undercount, and the archive and total
    counters below are published alongside them so the reader can see the
    whole tree reconcile: live + archived == total == filesystem.
    """

    vault_root: str
    telemetry_records: int = 0
    tokens_in_total: int = 0
    tokens_out_total: int = 0
    cost_usd_total: float = 0.0
    median_tokens_in: float = 0.0
    p95_tokens_in: float = 0.0
    staging_files: int = 0
    staging_bytes: int = 0
    staging_archived_files: int = 0
    staging_archived_bytes: int = 0
    staging_total_files: int = 0
    staging_total_bytes: int = 0
    # Shell-output headroom, measured on the payloads the capture path
    # actually compresses (see distill.shell_compress) rather than on raw
    # JSONL bytes. ``sampled_files`` is published so the estimate can be
    # weighed rather than taken on faith: zero means nothing was measured.
    staging_sampled_files: int = 0
    shell_payload_bytes: int = 0
    shell_payload_compressed_bytes: int = 0
    injection_tracker_sessions: int = 0
    injection_hits_total: int = 0
    injection_skips_total: int = 0
    recommendations: list[str] = field(default_factory=list)


def _resolve(explicit: Path | None) -> VaultConfig:
    if explicit is not None:
        return VaultConfig.from_path(explicit)
    return VaultConfig.resolve()


def _read_telemetry(vault: VaultConfig) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
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


@dataclass
class _StagingStats:
    """Internal carrier for the staging half of the report."""

    live_files: int = 0
    live_bytes: int = 0
    archived_files: int = 0
    archived_bytes: int = 0
    sampled_files: int = 0
    payload_bytes: int = 0
    payload_compressed_bytes: int = 0


def _sample_shell_payloads(files: list[Path], stats: _StagingStats) -> None:
    """Measure compression headroom on the newest staged records.

    The question this answers is "is shell output being compressed *now*",
    so the sample is taken from the most recently written files rather than
    from wherever the directory walk happened to start.

    Headroom is measured against the payloads ``iter_compressible_outputs``
    identifies — the same contract the capture path applies — not against
    raw JSONL bytes. Compressing a JSONL file as if it were shell output
    mostly measures the max-chars truncation of a long single-line JSON
    record, which is not a saving anyone can collect.
    """
    newest = sorted(files, key=lambda f: f.stat().st_mtime, reverse=True)
    for f in newest[:_SAMPLE_FILES]:
        try:
            text = f.read_text(encoding="utf-8")
        except OSError:
            continue
        stats.sampled_files += 1
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(rec, dict):
                continue
            for _key, value in iter_compressible_outputs(rec):
                result = compress_shell_output(value)
                stats.payload_bytes += result.original_bytes
                stats.payload_compressed_bytes += min(
                    result.compressed_bytes, result.original_bytes
                )


def _staging_stats(vault: VaultConfig) -> _StagingStats:
    """Split the staging tree into the live backlog and the archive."""
    stats = _StagingStats()
    if not vault.staging_dir.exists():
        return stats
    live: list[Path] = []
    for f in vault.staging_dir.rglob("*.jsonl"):
        try:
            size = f.stat().st_size
        except OSError:
            continue
        if "archive" in f.parts:
            stats.archived_files += 1
            stats.archived_bytes += size
            continue
        live.append(f)
        stats.live_files += 1
        stats.live_bytes += size
    if live:
        _sample_shell_payloads(live, stats)
    return stats


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
    # "Wire shell compression into your PostToolUse capture" used to be
    # emitted on backlog size alone, so it kept firing at operators whose
    # hook had been compressing all along — advice they had already taken,
    # which is how a report loses its reader. Speak only when the staged
    # payloads still hold recoverable headroom. Because compression is
    # idempotent, an already-compressed backlog measures near zero here and
    # this stays silent; nothing sampled also stays silent, since no
    # measurement is no finding.
    if report.shell_payload_bytes > 0:
        savings = report.shell_payload_bytes - report.shell_payload_compressed_bytes
        reduction = savings / report.shell_payload_bytes
        if reduction >= _SHELL_HEADROOM_FLOOR:
            out.append(
                f"distill.shell_compress projects ~{savings // 1024} KB savings "
                f"({reduction * 100:.0f}% reduction) across the shell output in "
                f"the {report.staging_sampled_files} most recent staging files. "
                "Wire it into your PostToolUse capture."
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
            "No major waste patterns detected. Run 'mneme-audit' again next "
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
    staging = _staging_stats(vault)
    report.staging_files = staging.live_files
    report.staging_bytes = staging.live_bytes
    report.staging_archived_files = staging.archived_files
    report.staging_archived_bytes = staging.archived_bytes
    report.staging_total_files = staging.live_files + staging.archived_files
    report.staging_total_bytes = staging.live_bytes + staging.archived_bytes
    report.staging_sampled_files = staging.sampled_files
    report.shell_payload_bytes = staging.payload_bytes
    report.shell_payload_compressed_bytes = staging.payload_compressed_bytes
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
