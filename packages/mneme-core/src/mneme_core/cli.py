"""``mneme`` CLI for mneme-core operations.

This CLI lives in mneme-core (not in mneme-cc-plugin) because the
commands here address the vault and its indexes directly: they do
not require Claude Code to be installed or running.

Top-level groups:

* ``mneme index``   FTS5 indexer subcommands (see ``mneme_core.fts5``).
* ``mneme kg``      Out-of-band knowledge-graph worker (Phase E).
* ``mneme version`` Print package version.

``mneme-cc-plugin`` re-uses the ``mneme`` console-script name for the
plugin-side install orchestrator. The two surfaces are intentionally
separate: plugin install runs once, vault operations run repeatedly.
The plugin CLI shadows this one if both packages are installed; that
is fine because the install CLI is the user-facing surface and this
one is invoked via the explicit module path ``python -m mneme_core``.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import click

from . import __version__
from .compression.config import (
    DEFAULT_COST_CAP_USD_MONTHLY,
    CompressionConfig,
    ensure_default_config,
    read_config,
    write_config,
)
from .compression.ledger import month_to_date_spend, rolling_30d_spend
from .compression.pipeline import (
    pipeline_config_from_vault,
    run_compression,
)
from .kg import (
    community_refresh,
    drain_dry_run,
    drain_live,
    kg_config_from_vault,
)
from .patterns import (
    Pattern,
    delete_pattern,
    list_patterns,
    load_pattern,
    search_patterns,
    store_pattern,
)
from .trajectory import (
    end_trajectory,
    list_trajectories,
    load_trajectory,
    record_step,
    start_trajectory,
)
from .vault.config import VaultConfig, VaultNotFoundError


def _resolve_vault(explicit: Path | None) -> VaultConfig:
    try:
        if explicit is not None:
            return VaultConfig.from_path(explicit)
        return VaultConfig.resolve()
    except VaultNotFoundError as exc:
        raise click.ClickException(str(exc)) from exc


@click.group(help="mneme-core operations CLI.")
@click.version_option(__version__, prog_name="mneme-core")
def cli() -> None:  # pragma: no cover - dispatcher
    pass


@cli.group(help="Knowledge-graph worker subcommands (full profile).")
def kg() -> None:  # pragma: no cover - dispatcher
    pass


@kg.command("dry-run", help="Parse the KG queue and report counts. No LLM, no Neo4j.")
@click.option(
    "--vault",
    "vault_root",
    type=click.Path(file_okay=False, path_type=Path),
    default=None,
)
def kg_dry_run(vault_root: Path | None) -> None:
    vault = _resolve_vault(vault_root)
    cfg = kg_config_from_vault(vault)
    result = drain_dry_run(cfg)
    click.echo(json.dumps(result, indent=2, ensure_ascii=False))


@kg.command("drain", help="Drain the queue into Graphiti. Requires LLM credit + Neo4j.")
@click.option(
    "--vault",
    "vault_root",
    type=click.Path(file_okay=False, path_type=Path),
    default=None,
)
@click.option(
    "--cost-cap-usd-monthly",
    type=float,
    default=None,
    help="Stop adding episodes when month-to-date spend exceeds this cap.",
)
@click.option(
    "--per-episode-usd-estimate",
    type=float,
    default=0.002,
    show_default=True,
    help="Rough per-add cost used for cap enforcement.",
)
def kg_drain(
    vault_root: Path | None,
    cost_cap_usd_monthly: float | None,
    per_episode_usd_estimate: float,
) -> None:
    vault = _resolve_vault(vault_root)
    cfg = kg_config_from_vault(vault, cost_cap_usd_monthly=cost_cap_usd_monthly)
    result = drain_live(cfg, per_episode_usd_estimate=per_episode_usd_estimate)
    click.echo(json.dumps(result, indent=2, ensure_ascii=False))
    if "error" in result:
        sys.exit(1)


@kg.command("community", help="Rebuild Graphiti communities. Clears the refresh flag.")
@click.option(
    "--vault",
    "vault_root",
    type=click.Path(file_okay=False, path_type=Path),
    default=None,
)
def kg_community(vault_root: Path | None) -> None:
    vault = _resolve_vault(vault_root)
    cfg = kg_config_from_vault(vault)
    result = community_refresh(cfg)
    click.echo(json.dumps(result, indent=2, ensure_ascii=False))
    if "error" in result:
        sys.exit(1)


@cli.group(help="Background AI compression subcommands (opt-in).")
def compress() -> None:  # pragma: no cover - dispatcher
    pass


@compress.command("enable", help="Turn on background compression for this vault.")
@click.option(
    "--vault",
    "vault_root",
    type=click.Path(file_okay=False, path_type=Path),
    default=None,
)
@click.option(
    "--cost-cap-usd-monthly",
    type=float,
    default=DEFAULT_COST_CAP_USD_MONTHLY,
    show_default=True,
    help="Month-to-date USD ceiling. Run aborts above this.",
)
@click.option(
    "--model",
    type=str,
    default=None,
    help="LLM model identifier. Defaults to the config or the package default.",
)
def compress_enable(
    vault_root: Path | None,
    cost_cap_usd_monthly: float,
    model: str | None,
) -> None:
    vault = _resolve_vault(vault_root)
    existing = ensure_default_config(vault.compression_config_path)
    cfg = CompressionConfig(
        enabled=True,
        cost_cap_usd_monthly=cost_cap_usd_monthly,
        model=model or existing.model,
        max_tokens=existing.max_tokens,
        max_payload_bytes=existing.max_payload_bytes,
    )
    write_config(vault.compression_config_path, cfg)
    click.echo(
        json.dumps(
            {
                "status": "enabled",
                "config_path": str(vault.compression_config_path),
                "cost_cap_usd_monthly": cfg.cost_cap_usd_monthly,
                "model": cfg.model,
            },
            indent=2,
        )
    )


@compress.command("disable", help="Turn off background compression. Keeps config.")
@click.option(
    "--vault",
    "vault_root",
    type=click.Path(file_okay=False, path_type=Path),
    default=None,
)
def compress_disable(vault_root: Path | None) -> None:
    vault = _resolve_vault(vault_root)
    current = ensure_default_config(vault.compression_config_path)
    current.enabled = False
    write_config(vault.compression_config_path, current)
    click.echo(
        json.dumps(
            {"status": "disabled", "config_path": str(vault.compression_config_path)},
            indent=2,
        )
    )


@compress.command("status", help="Report config, pause flag, and month-to-date spend.")
@click.option(
    "--vault",
    "vault_root",
    type=click.Path(file_okay=False, path_type=Path),
    default=None,
)
def compress_status(vault_root: Path | None) -> None:
    vault = _resolve_vault(vault_root)
    cfg = read_config(vault.compression_config_path)
    report = {
        "enabled": cfg.enabled,
        "cost_cap_usd_monthly": cfg.cost_cap_usd_monthly,
        "model": cfg.model,
        "config_path": str(vault.compression_config_path),
        "pause_flag_present": vault.compression_pause_flag.is_file(),
        "ledger_path": str(vault.kg_cost_ledger),
        "spend_month_to_date_usd": round(
            month_to_date_spend(vault.kg_cost_ledger), 6
        ),
        "spend_rolling_30d_usd": round(
            rolling_30d_spend(vault.kg_cost_ledger), 6
        ),
    }
    click.echo(json.dumps(report, indent=2, default=str))


@compress.command("dry-run", help="Plan a run: load staged events, no LLM call.")
@click.option(
    "--vault",
    "vault_root",
    type=click.Path(file_okay=False, path_type=Path),
    default=None,
)
def compress_dry_run(vault_root: Path | None) -> None:
    vault = _resolve_vault(vault_root)
    cfg = read_config(vault.compression_config_path)
    pipeline_cfg = pipeline_config_from_vault(vault, cfg)
    # ``run_compression`` honors the disabled gate inside, but dry-run
    # should still report what would happen even when disabled. Force
    # enable purely for the dry-run accounting.
    enable_override = CompressionConfig(
        enabled=True,
        cost_cap_usd_monthly=cfg.cost_cap_usd_monthly,
        model=cfg.model,
        max_tokens=cfg.max_tokens,
        max_payload_bytes=cfg.max_payload_bytes,
    )
    pipeline_cfg.compression = enable_override
    report = run_compression(pipeline_cfg, dry_run=True)
    click.echo(json.dumps(report.__dict__, indent=2, default=str))


@compress.command("run", help="Execute one compression pass against staged events.")
@click.option(
    "--vault",
    "vault_root",
    type=click.Path(file_okay=False, path_type=Path),
    default=None,
)
def compress_run(vault_root: Path | None) -> None:
    vault = _resolve_vault(vault_root)
    cfg = read_config(vault.compression_config_path)
    pipeline_cfg = pipeline_config_from_vault(vault, cfg)
    report = run_compression(pipeline_cfg)
    click.echo(json.dumps(report.__dict__, indent=2, default=str))
    if report.status in {"provider_error", "error", "write_failed"}:
        sys.exit(1)


@cli.group("patterns", help="Reusable action-pattern memory subcommands.")
def patterns_group() -> None:  # pragma: no cover - dispatcher
    pass


@patterns_group.command("store", help="Write one pattern to the vault.")
@click.option(
    "--vault",
    "vault_root",
    type=click.Path(file_okay=False, path_type=Path),
    default=None,
)
@click.option("--name", required=True, type=str)
@click.option("--signal", required=True, type=str, help="Triggering condition.")
@click.option("--action", required=True, type=str, help="What to do.")
@click.option("--outcome", default="", type=str, help="Observed result.")
@click.option("--tag", "tags", multiple=True, type=str)
def patterns_store(
    vault_root: Path | None,
    name: str,
    signal: str,
    action: str,
    outcome: str,
    tags: tuple[str, ...],
) -> None:
    vault = _resolve_vault(vault_root)
    pattern = Pattern(
        name=name,
        signal=signal,
        action=action,
        outcome=outcome,
        tags=list(tags),
    )
    path = store_pattern(vault, pattern)
    click.echo(json.dumps({"status": "stored", "path": str(path)}, indent=2))


@patterns_group.command("search", help="Find patterns whose text matches the query.")
@click.option(
    "--vault",
    "vault_root",
    type=click.Path(file_okay=False, path_type=Path),
    default=None,
)
@click.option("--query", required=True, type=str)
@click.option("--top-k", default=10, show_default=True, type=int)
def patterns_search(
    vault_root: Path | None, query: str, top_k: int
) -> None:
    vault = _resolve_vault(vault_root)
    hits = search_patterns(vault, query, top_k=top_k)
    payload = [
        {
            "name": h.pattern.name,
            "score": h.score,
            "signal": h.pattern.signal,
            "tags": h.pattern.tags,
        }
        for h in hits
    ]
    click.echo(json.dumps(payload, indent=2, ensure_ascii=False))


@patterns_group.command("list", help="Enumerate every pattern in the vault.")
@click.option(
    "--vault",
    "vault_root",
    type=click.Path(file_okay=False, path_type=Path),
    default=None,
)
def patterns_list(vault_root: Path | None) -> None:
    vault = _resolve_vault(vault_root)
    patterns = list_patterns(vault)
    payload = [
        {"name": p.name, "tags": p.tags, "created_at": p.created_at}
        for p in patterns
    ]
    click.echo(json.dumps(payload, indent=2, ensure_ascii=False))


@patterns_group.command("show", help="Print one pattern by name.")
@click.option(
    "--vault",
    "vault_root",
    type=click.Path(file_okay=False, path_type=Path),
    default=None,
)
@click.option("--name", required=True, type=str)
def patterns_show(vault_root: Path | None, name: str) -> None:
    vault = _resolve_vault(vault_root)
    pattern = load_pattern(vault, name)
    if pattern is None:
        raise click.ClickException(f"Pattern not found: {name}")
    click.echo(
        json.dumps(
            {
                "name": pattern.name,
                "signal": pattern.signal,
                "action": pattern.action,
                "outcome": pattern.outcome,
                "tags": pattern.tags,
                "created_at": pattern.created_at,
            },
            indent=2,
            ensure_ascii=False,
        )
    )


@patterns_group.command("delete", help="Remove one pattern by name.")
@click.option(
    "--vault",
    "vault_root",
    type=click.Path(file_okay=False, path_type=Path),
    default=None,
)
@click.option("--name", required=True, type=str)
def patterns_delete(vault_root: Path | None, name: str) -> None:
    vault = _resolve_vault(vault_root)
    removed = delete_pattern(vault, name)
    click.echo(json.dumps({"status": "removed" if removed else "not_found"}))


@cli.group("trajectory", help="Per-session trajectory recorder subcommands.")
def trajectory_group() -> None:  # pragma: no cover - dispatcher
    pass


@trajectory_group.command("start", help="Open a new trajectory file for a session.")
@click.option(
    "--vault",
    "vault_root",
    type=click.Path(file_okay=False, path_type=Path),
    default=None,
)
@click.option("--session-id", required=True, type=str)
def trajectory_start(vault_root: Path | None, session_id: str) -> None:
    vault = _resolve_vault(vault_root)
    path = start_trajectory(vault, session_id)
    if path is None:
        raise click.ClickException("Failed to start trajectory.")
    click.echo(json.dumps({"status": "started", "path": str(path)}, indent=2))


@trajectory_group.command("step", help="Append one action/observation to a trajectory.")
@click.option(
    "--vault",
    "vault_root",
    type=click.Path(file_okay=False, path_type=Path),
    default=None,
)
@click.option("--session-id", required=True, type=str)
@click.option("--action", required=True, type=str)
@click.option("--input-summary", default="", type=str)
@click.option("--observation", default="", type=str)
def trajectory_step(
    vault_root: Path | None,
    session_id: str,
    action: str,
    input_summary: str,
    observation: str,
) -> None:
    vault = _resolve_vault(vault_root)
    ok = record_step(
        vault,
        session_id,
        action=action,
        input_summary=input_summary,
        observation=observation,
    )
    click.echo(json.dumps({"status": "recorded" if ok else "failed"}))
    if not ok:
        sys.exit(1)


@trajectory_group.command("end", help="Seal a trajectory by stamping ended_at.")
@click.option(
    "--vault",
    "vault_root",
    type=click.Path(file_okay=False, path_type=Path),
    default=None,
)
@click.option("--session-id", required=True, type=str)
def trajectory_end(vault_root: Path | None, session_id: str) -> None:
    vault = _resolve_vault(vault_root)
    ok = end_trajectory(vault, session_id)
    click.echo(json.dumps({"status": "sealed" if ok else "not_found"}))
    if not ok:
        sys.exit(1)


@trajectory_group.command("show", help="Print one trajectory by session id.")
@click.option(
    "--vault",
    "vault_root",
    type=click.Path(file_okay=False, path_type=Path),
    default=None,
)
@click.option("--session-id", required=True, type=str)
def trajectory_show(vault_root: Path | None, session_id: str) -> None:
    vault = _resolve_vault(vault_root)
    traj = load_trajectory(vault, session_id)
    if traj is None:
        raise click.ClickException(f"Trajectory not found: {session_id}")
    payload = {
        "session_id": traj.session_id,
        "started_at": traj.started_at,
        "ended_at": traj.ended_at,
        "steps": [s.__dict__ for s in traj.steps],
    }
    click.echo(json.dumps(payload, indent=2, ensure_ascii=False))


@trajectory_group.command("list", help="Enumerate trajectories by date range.")
@click.option(
    "--vault",
    "vault_root",
    type=click.Path(file_okay=False, path_type=Path),
    default=None,
)
@click.option("--date-from", default=None, type=str)
@click.option("--date-to", default=None, type=str)
def trajectory_list(
    vault_root: Path | None,
    date_from: str | None,
    date_to: str | None,
) -> None:
    vault = _resolve_vault(vault_root)
    trajs = list_trajectories(vault, date_from=date_from, date_to=date_to)
    payload = [
        {
            "session_id": t.session_id,
            "started_at": t.started_at,
            "ended_at": t.ended_at,
            "steps_count": len(t.steps),
        }
        for t in trajs
    ]
    click.echo(json.dumps(payload, indent=2, ensure_ascii=False))


@cli.group(help="FTS5 index subcommands.")
def index() -> None:  # pragma: no cover - dispatcher
    pass


@index.command("rebuild", help="Rebuild the FTS5 index over every markdown file in the vault.")
@click.option(
    "--vault",
    "vault_root",
    type=click.Path(file_okay=False, path_type=Path),
    default=None,
)
@click.option(
    "--locale",
    type=click.Choice(["en", "tr"]),
    default="en",
    show_default=True,
    help=(
        "Token normalizer locale. 'tr' enables the Turkish casefold "
        "for KIYASLAMA-style edge cases."
    ),
)
def index_rebuild(vault_root: Path | None, locale: str) -> None:
    from mneme_core.fts5 import indexer as fts5_indexer

    vault = _resolve_vault(vault_root)
    if locale == "tr":
        from mneme_core.fts5.locale.tr import normalize_tr
        normalize = normalize_tr
    else:
        normalize = None
    cfg = fts5_indexer.IndexerConfig(
        vault_root=vault.root,
        db_path=vault.fts5_db,
        normalize=normalize if normalize is not None else fts5_indexer._identity,
    )
    conn = fts5_indexer.connect(vault.fts5_db)
    try:
        fts5_indexer.ensure_schema(conn)
        stats = fts5_indexer.index_vault(conn, cfg)
    finally:
        conn.close()
    click.echo(
        json.dumps(
            {
                "vault": str(vault.root),
                "db": str(vault.fts5_db),
                "locale": locale,
                "stats": {
                    "indexed": stats.indexed,
                    "skipped_excluded": stats.skipped_excluded,
                    "skipped_unchanged": stats.skipped_unchanged,
                    "skipped_error": stats.skipped_error,
                    "total_seen": stats.total_seen,
                },
            },
            indent=2,
            ensure_ascii=False,
        )
    )


@index.command("stats", help="Report row counts and indexed-at timestamps for the FTS5 index.")
@click.option(
    "--vault",
    "vault_root",
    type=click.Path(file_okay=False, path_type=Path),
    default=None,
)
def index_stats(vault_root: Path | None) -> None:
    import sqlite3 as _sqlite3
    vault = _resolve_vault(vault_root)
    if not vault.fts5_db.exists():
        click.echo(
            json.dumps(
                {"status": "missing", "db": str(vault.fts5_db)},
                indent=2,
            )
        )
        sys.exit(1)
    conn = _sqlite3.connect(vault.fts5_db)
    try:
        docs = conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0]
        first = conn.execute(
            "SELECT MIN(indexed_at), MAX(indexed_at) FROM documents"
        ).fetchone()
        by_type_rows = conn.execute(
            "SELECT frontmatter_type, COUNT(*) FROM documents "
            "GROUP BY frontmatter_type ORDER BY 2 DESC"
        ).fetchall()
    finally:
        conn.close()
    click.echo(
        json.dumps(
            {
                "status": "ok",
                "vault": str(vault.root),
                "db": str(vault.fts5_db),
                "documents": docs,
                "indexed_at_earliest": first[0],
                "indexed_at_latest": first[1],
                "by_frontmatter_type": [
                    {"type": row[0] or "(none)", "count": row[1]} for row in by_type_rows
                ],
            },
            indent=2,
            ensure_ascii=False,
        )
    )


@cli.command("version", help="Print mneme-core version.")
def version_cmd() -> None:
    click.echo(__version__)


def main() -> None:
    cli(prog_name="mneme-core")


if __name__ == "__main__":  # pragma: no cover
    main()
