"""``mneme`` CLI for mneme-core operations.

This CLI lives in mneme-core (not in mneme-cc-plugin) because the
commands here address the vault and its indexes directly: they do
not require Claude Code to be installed or running.

Top-level groups:

* ``mneme index``   FTS5 indexer subcommands (see ``mneme_core.fts5``).
* ``mneme kg``      Out-of-band knowledge-graph worker (full profile).
* ``mneme audit-log`` Privacy redaction audit reader.
* ``mneme version`` Print package version.

``mneme-cc-plugin`` re-uses the ``mneme`` console-script name for the
plugin-side install orchestrator. The plugin CLI imports and exposes
these same command objects so normal installs still have one public
``mneme`` surface.
"""

from __future__ import annotations

import json
import sys
from datetime import date
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


def _audit_since_date(value: str | None) -> str | None:
    if value is None:
        return None
    if value == "today":
        return date.today().isoformat()
    return value


@cli.command("audit-log", help="Inspect privacy redaction audit JSONL entries.")
@click.option(
    "--vault",
    "vault_root",
    type=click.Path(file_okay=False, path_type=Path),
    default=None,
)
@click.option(
    "--since",
    type=str,
    default=None,
    help="YYYY-MM-DD lower bound, or 'today'.",
)
@click.option("--limit", type=int, default=100, show_default=True)
def audit_log(vault_root: Path | None, since: str | None, limit: int) -> None:
    vault = _resolve_vault(vault_root)
    since_date = _audit_since_date(since)
    entries: list[dict[str, object]] = []
    if vault.audit_log_dir.is_dir():
        for path in sorted(vault.audit_log_dir.glob("*.jsonl")):
            if since_date is not None and path.stem < since_date:
                continue
            try:
                lines = path.read_text(encoding="utf-8").splitlines()
            except OSError:
                continue
            for line in lines:
                if not line.strip():
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    record = {"_parse_error": True, "raw": line}
                if isinstance(record, dict):
                    entries.append(record)
    if limit >= 0:
        entries = entries[-limit:]
    click.echo(
        json.dumps(
            {
                "audit_dir": str(vault.audit_log_dir),
                "since": since_date,
                "count": len(entries),
                "entries": entries,
            },
            indent=2,
            ensure_ascii=False,
        )
    )


@cli.command("doctor", help="Run vault health checks and print a JSON report.")
@click.option(
    "--vault",
    "vault_root",
    type=click.Path(file_okay=False, path_type=Path),
    default=None,
)
@click.option(
    "--json/--no-json",
    "as_json",
    default=True,
    show_default=True,
    help="Emit output as JSON (default) or plain text.",
)
def doctor(vault_root: Path | None, as_json: bool) -> None:
    """Check vault health: index presence, schema version, row count, config."""
    import sqlite3 as _sqlite3

    from .fts5.indexer import SCHEMA_VERSION as _SCHEMA_VERSION

    CheckResult = dict[str, str]  # {name, status, detail}

    def _check(
        name: str,
        status: str,
        detail: str,
    ) -> CheckResult:
        return {"name": name, "status": status, "detail": detail}

    checks: list[CheckResult] = []

    # --- 1. vault_resolves ---
    try:
        vault = _resolve_vault(vault_root)
        vault_ok = vault.root.is_dir()
        if vault_ok:
            checks.append(
                _check(
                    "vault_resolves",
                    "ok",
                    str(vault.root),
                )
            )
        else:
            checks.append(
                _check(
                    "vault_resolves",
                    "fail",
                    f"vault root does not exist or is not a directory: {vault.root}",
                )
            )
    except Exception as exc:  # noqa: BLE001
        checks.append(_check("vault_resolves", "fail", str(exc)))
        overall = "fail"
        report: dict[str, object] = {"overall": overall, "checks": checks}
        click.echo(json.dumps(report, indent=2, ensure_ascii=False))
        sys.exit(1)

    db_path = vault.fts5_db
    db_exists = db_path.exists()

    # --- 2. index_present ---
    if db_exists:
        checks.append(_check("index_present", "ok", str(db_path)))
    else:
        checks.append(
            _check(
                "index_present",
                "warn",
                f"FTS5 index not found at {db_path}; run: mneme index rebuild",
            )
        )

    # --- 3. index_schema ---
    if db_exists:
        try:
            conn = _sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
            try:
                row = conn.execute(
                    "SELECT MAX(schema_version) FROM documents"
                ).fetchone()
                stored_ver = row[0] if row and row[0] is not None else "unknown"
                if stored_ver == _SCHEMA_VERSION:
                    checks.append(
                        _check(
                            "index_schema",
                            "ok",
                            f"schema_version={stored_ver}",
                        )
                    )
                else:
                    checks.append(
                        _check(
                            "index_schema",
                            "warn",
                            (
                                f"stored schema_version={stored_ver!r} "
                                f"!= expected {_SCHEMA_VERSION!r}; "
                                "run: mneme index rebuild to migrate"
                            ),
                        )
                    )
            finally:
                conn.close()
        except Exception as exc:  # noqa: BLE001
            checks.append(_check("index_schema", "fail", str(exc)))
    else:
        checks.append(_check("index_schema", "na", "index absent — skipped"))

    # --- 4. index_freshness ---
    if db_exists:
        try:
            conn = _sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
            try:
                row_count_row = conn.execute(
                    "SELECT COUNT(*) FROM documents"
                ).fetchone()
                row_count = row_count_row[0] if row_count_row else 0
                max_indexed_at_row = conn.execute(
                    "SELECT MAX(indexed_at) FROM documents"
                ).fetchone()
                max_indexed_at = (
                    max_indexed_at_row[0]
                    if max_indexed_at_row and max_indexed_at_row[0] is not None
                    else None
                )
            finally:
                conn.close()
            detail = f"documents={row_count}, max_indexed_at={max_indexed_at}"
            if row_count == 0:
                checks.append(
                    _check(
                        "index_freshness",
                        "warn",
                        f"index is empty ({detail}); run: mneme index rebuild",
                    )
                )
            else:
                checks.append(_check("index_freshness", "ok", detail))
        except Exception as exc:  # noqa: BLE001
            checks.append(_check("index_freshness", "fail", str(exc)))
    else:
        checks.append(_check("index_freshness", "na", "index absent — skipped"))

    # --- 5. compression_config ---
    try:
        config_exists = vault.compression_config_path.exists()
        checks.append(
            _check(
                "compression_config",
                "ok" if config_exists else "na",
                str(vault.compression_config_path)
                if config_exists
                else "not configured (opt-in feature)",
            )
        )
    except Exception as exc:  # noqa: BLE001
        checks.append(_check("compression_config", "warn", str(exc)))

    # --- 6. frontmatter_dates ---
    # Walk every markdown file in the vault and report files that contain
    # a ``created`` or ``modified`` field that cannot be parsed as ISO 8601.
    # One bad note must not abort the walk; parsing errors are accumulated.
    try:
        import yaml as _yaml

        _EXCLUDE = (
            "/.git/",
            "/node_modules/",
            "/.claude/",
            "/.obsidian/",
            "/.trash/",
            "/.idea/",
            "/__pycache__/",
            "/.mneme/",
        )
        _FRONTMATTER_DELIM = "---"

        def _has_bad_date(md_path: Path) -> list[str]:
            """Return a list of field names with unparseable timestamps."""
            try:
                text = md_path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                return []
            if not text.startswith(_FRONTMATTER_DELIM):
                return []
            lines = text.split("\n")
            if len(lines) < 2 or lines[0].strip() != _FRONTMATTER_DELIM:
                return []
            closing = -1
            for i in range(1, len(lines)):
                if lines[i].strip() == _FRONTMATTER_DELIM:
                    closing = i
                    break
            if closing == -1:
                return []
            yaml_block = "\n".join(lines[1:closing])
            try:
                from .vault.frontmatter import _parse_dt
                from .vault.frontmatter import load_yaml_block as _load_yaml_block
                data = _load_yaml_block(yaml_block) or {}
            except (_yaml.YAMLError, ValueError):
                return []
            if not isinstance(data, dict):
                return []

            bad: list[str] = []
            for field_name in ("created", "modified"):
                raw = data.get(field_name)
                if raw is not None and _parse_dt(raw) is None:
                    bad.append(field_name)
            return bad

        bad_notes: list[str] = []
        for md_path in vault.root.rglob("*.md"):
            rel = "/" + str(md_path.relative_to(vault.root)).replace("\\", "/") + "/"
            if any(p in rel for p in _EXCLUDE):
                continue
            try:
                bad_fields = _has_bad_date(md_path)
            except Exception:  # noqa: BLE001 - one bad file must not abort the walk
                bad_fields = []
            if bad_fields:
                bad_notes.append(
                    f"{md_path.relative_to(vault.root).as_posix()}"
                    f" (fields: {', '.join(bad_fields)})"
                )

        if bad_notes:
            checks.append(
                _check(
                    "frontmatter_dates",
                    "warn",
                    f"{len(bad_notes)} note(s) with unparseable date fields"
                    f" — run 'mneme doctor' to list; fix manually:"
                    f" {'; '.join(bad_notes)}",
                )
            )
        else:
            checks.append(
                _check(
                    "frontmatter_dates",
                    "ok",
                    "all frontmatter date fields parse cleanly",
                )
            )
    except Exception as exc:  # noqa: BLE001
        checks.append(_check("frontmatter_dates", "warn", str(exc)))

    # Derive overall status: fail > warn > ok.
    statuses = {c["status"] for c in checks}
    if "fail" in statuses:
        overall = "fail"
    elif "warn" in statuses:
        overall = "warn"
    else:
        overall = "ok"

    report = {"overall": overall, "checks": checks}
    click.echo(json.dumps(report, indent=2, ensure_ascii=False))
    if overall == "fail":
        sys.exit(1)


@cli.command("version", help="Print mneme-core version.")
def version_cmd() -> None:
    click.echo(__version__)


def main() -> None:
    cli(prog_name="mneme-core")


if __name__ == "__main__":  # pragma: no cover
    main()
