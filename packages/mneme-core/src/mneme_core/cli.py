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
import tomllib
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


def _resolve_exclude_patterns(
    vault_root: Path, extra: tuple[str, ...] = ()
) -> tuple[str, ...]:
    """Built-in exclusions, plus this vault's own, plus any passed on the CLI.

    The built-in list is a module constant with no per-vault override, which
    is fine until a vault mirrors something large that is not its own content.
    Measured on a real 12,352-document vault: 57% of the index was third-party
    material — a mirrored plugin home alone accounted for 56.5% — competing
    with the operator's own notes for BM25 rank. There was no way to keep it
    out short of editing the constant.

    Entries are ADDED, never substituted. Letting a config file replace the
    defaults would let one typo put ``.git`` and ``node_modules`` back into
    the index, and no vault benefits from indexing those. Patterns are matched
    as substrings of ``/relative/path/``, so ``/vendor/`` excludes a directory
    while ``vendor`` would also catch ``my-vendor-notes.md`` — the slashes are
    the caller's to supply, exactly as in ``DEFAULT_EXCLUDE_PATTERNS``.

    A malformed config is reported, never silently ignored: a swallowed
    exclusion list looks identical to one that did nothing.
    """
    from mneme_core.fts5.indexer import DEFAULT_EXCLUDE_PATTERNS

    patterns: list[str] = list(DEFAULT_EXCLUDE_PATTERNS)
    config_path = vault_root / ".mneme" / "config.toml"
    if config_path.is_file():
        try:
            with config_path.open("rb") as fh:
                cfg = tomllib.load(fh)
        except (OSError, tomllib.TOMLDecodeError) as exc:
            raise click.ClickException(
                f"{config_path} could not be read: {exc}"
            ) from exc
        section = cfg.get("index")
        if section is not None:
            if not isinstance(section, dict):
                raise click.ClickException(
                    f"{config_path}: [index] must be a table, got {type(section).__name__}"
                )
            declared = section.get("exclude", [])
            if not isinstance(declared, list) or not all(
                isinstance(item, str) for item in declared
            ):
                raise click.ClickException(
                    f"{config_path}: [index] exclude must be a list of strings"
                )
            patterns.extend(declared)
    patterns.extend(extra)
    # Order-preserving dedupe so the reported list mirrors what actually ran.
    return tuple(dict.fromkeys(patterns))


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
@click.option(
    "--exclude",
    "extra_excludes",
    multiple=True,
    metavar="SUBSTRING",
    help=(
        "Extra path substring to skip, repeatable. Added to the built-in list "
        "and to any [index] exclude entries in the vault's config.toml; it "
        "never replaces them."
    ),
)
def index_rebuild(
    vault_root: Path | None, locale: str, extra_excludes: tuple[str, ...]
) -> None:
    from mneme_core.fts5 import indexer as fts5_indexer

    vault = _resolve_vault(vault_root)
    excludes = _resolve_exclude_patterns(vault.root, extra_excludes)
    if locale == "tr":
        from mneme_core.fts5.locale.tr import (
            normalize_tr,
            normalize_tr_ascii_fold,
            normalize_tr_ascii_fold_for_fts,
            normalize_tr_for_fts,
        )

        normalize = normalize_tr
        normalize_for_fts = normalize_tr_for_fts
        normalize_ascii = normalize_tr_ascii_fold
        normalize_ascii_for_fts = normalize_tr_ascii_fold_for_fts
    else:
        from mneme_core.fts5.locale.en import normalize_en, normalize_en_for_fts

        normalize = normalize_en
        normalize_for_fts = normalize_en_for_fts
        # No ASCII-fold sibling for English. That second key exists solely to
        # bridge the Turkish dotted/dotless ``i``; English has no such
        # ambiguity, so enabling it would double the index for no recall.
        normalize_ascii = None
        normalize_ascii_for_fts = None
    cfg = fts5_indexer.IndexerConfig(
        vault_root=vault.root,
        db_path=vault.fts5_db,
        normalize=normalize,
        normalize_for_fts=normalize_for_fts,
        normalize_ascii=normalize_ascii,
        normalize_ascii_for_fts=normalize_ascii_for_fts,
        exclude_patterns=excludes,
    )
    conn = fts5_indexer.connect(vault.fts5_db)
    try:
        fts5_indexer.ensure_schema(conn)
        stats = fts5_indexer.index_vault(conn, cfg)
    finally:
        conn.close()
    # SQLite keeps freed pages allocated, so a rebuild that drops documents
    # leaves the file its old size. Measured: excluding 9,414 mirrored
    # documents took the index from 12,390 rows to 2,976 and the file from
    # 655 MB to 679 MB — larger, on a quarter of the content. VACUUM returned
    # it to 425 MB. A rebuild exists to produce a clean derived artifact, so
    # reclaiming is part of the job rather than a separate chore the user has
    # to know about.
    import sqlite3 as _sqlite3_reclaim

    reclaim_conn = _sqlite3_reclaim.connect(vault.fts5_db)
    try:
        reclaim_conn.execute("VACUUM")
    finally:
        reclaim_conn.close()
    click.echo(
        json.dumps(
            {
                "vault": str(vault.root),
                "db": str(vault.fts5_db),
                "db_size_bytes": vault.fts5_db.stat().st_size,
                "locale": locale,
                "exclude_patterns": list(excludes),
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


@cli.group("retrieval", help="Retrieval pipeline subcommands.")
def retrieval_group() -> None:  # pragma: no cover - dispatcher
    pass


@retrieval_group.command("stats", help="Summarise per-backend retrieval telemetry from JSONL files.")  # noqa: E501
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
    help="YYYY-MM-DD lower bound. Only files on or after this date are read.",
)
def retrieval_stats(vault_root: Path | None, since: str | None) -> None:
    """Read retrieval-YYYY-MM-DD.jsonl files and print a summary JSON.

    Summary includes: total_queries, per-backend hit counts, and p50/p95
    latency per backend in milliseconds.
    """
    vault = _resolve_vault(vault_root)
    telemetry_dir = vault.telemetry_dir

    total_queries = 0
    by_backend_hits: dict[str, int] = {}
    by_backend_latencies: dict[str, list[float]] = {}

    if telemetry_dir.is_dir():
        for jsonl_path in sorted(telemetry_dir.glob("retrieval-*.jsonl")):
            # Extract date from filename: retrieval-YYYY-MM-DD.jsonl
            stem = jsonl_path.stem  # "retrieval-YYYY-MM-DD"
            parts = stem.split("-", 1)
            file_date = parts[1] if len(parts) == 2 else ""
            if since is not None and file_date < since:
                continue
            try:
                lines = jsonl_path.read_text(encoding="utf-8").splitlines()
            except OSError:
                continue
            for line in lines:
                line = line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(record, dict):
                    continue
                total_queries += 1
                backends = record.get("backends", [])
                hits = record.get("per_backend_hits", {})
                latencies = record.get("per_backend_latency_ms", {})
                for backend in backends:
                    if not isinstance(backend, str):
                        continue
                    hit_count = hits.get(backend, 0) if isinstance(hits, dict) else 0
                    by_backend_hits[backend] = by_backend_hits.get(backend, 0) + hit_count
                    lat = latencies.get(backend) if isinstance(latencies, dict) else None
                    if isinstance(lat, (int, float)):
                        by_backend_latencies.setdefault(backend, []).append(float(lat))

    def _percentile(data: list[float], pct: float) -> float | None:
        if not data:
            return None
        sorted_data = sorted(data)
        idx = (pct / 100.0) * (len(sorted_data) - 1)
        lo = int(idx)
        hi = min(lo + 1, len(sorted_data) - 1)
        return sorted_data[lo] + (idx - lo) * (sorted_data[hi] - sorted_data[lo])

    latency_summary: dict[str, dict[str, float | None]] = {}
    for backend, lats in by_backend_latencies.items():
        latency_summary[backend] = {
            "p50_ms": _percentile(lats, 50),
            "p95_ms": _percentile(lats, 95),
            "samples": len(lats),
        }

    report = {
        "total_queries": total_queries,
        "by_backend_hits": by_backend_hits,
        "by_backend_latency_ms": latency_summary,
        "telemetry_dir": str(telemetry_dir),
        "since": since,
    }
    click.echo(json.dumps(report, indent=2, ensure_ascii=False))


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


def _verify_isolation_boundaries() -> list[dict[str, str]]:
    """Exercise scope and redaction invariants in a disposable vault.

    Every path used by this verification is derived from a system temporary
    directory. The operator vault is deliberately not accepted as an input,
    which keeps this write-heavy fixture isolated from user-owned data.
    """
    import tempfile
    from dataclasses import replace

    from .compression.config import CompressionConfig
    from .compression.llm import CompressionResult, LlmCallSpec
    from .compression.pipeline import pipeline_config_from_vault, run_compression
    from .fts5.indexer import IndexerConfig, connect, ensure_schema, index_vault

    scope_token = "mnemeisolationfixturetoken"
    scope_secrets = (
        "MNEME_DOCTOR_ALPHA_STORAGE_SECRET",
        "MNEME_DOCTOR_BETA_STORAGE_SECRET",
    )
    provider_input_secret = "MNEME_DOCTOR_PROVIDER_INPUT_SECRET"
    prompt_secret = "MNEME_DOCTOR_PROVIDER_PROMPT_SECRET"
    provider_output_secret = "MNEME_DOCTOR_PROVIDER_OUTPUT_SECRET"
    checks: list[dict[str, str]] = []

    def result(name: str, passed: bool, detail: str) -> dict[str, str]:
        return {
            "name": name,
            "status": "ok" if passed else "fail",
            "detail": detail,
        }

    class IsolationProvider:
        def __init__(self) -> None:
            self.last_spec: LlmCallSpec | None = None

        def compress(self, spec: LlmCallSpec) -> CompressionResult:
            self.last_spec = spec
            return CompressionResult(
                text=(
                    "Visible provider result "
                    f"<private>{provider_output_secret}</private>."
                ),
                tokens_in=1,
                tokens_out=1,
                model=spec.model,
            )

    with tempfile.TemporaryDirectory(prefix="mneme-doctor-isolation-") as raw_tmp:
        fixture_root = Path(raw_tmp) / "vault"
        fixture_root.mkdir()
        fixture = VaultConfig.from_path(fixture_root)

        alpha_path = fixture.root / "alpha.md"
        beta_path = fixture.root / "beta.md"
        alpha_path.write_text(
            "---\n"
            "id: isolation-alpha\n"
            "type: observation\n"
            "scope: alpha\n"
            "---\n\n"
            f"# Alpha\n\n{scope_token} "
            f"<private>{scope_secrets[0]}</private>\n",
            encoding="utf-8",
        )
        beta_path.write_text(
            "---\n"
            "id: isolation-beta\n"
            "type: observation\n"
            "scope: beta\n"
            "---\n\n"
            f"# Beta\n\n{scope_token} "
            f"<private>{scope_secrets[1]}</private>\n",
            encoding="utf-8",
        )

        try:
            conn = connect(fixture.fts5_db)
            try:
                ensure_schema(conn)
                stats = index_vault(
                    conn,
                    IndexerConfig(
                        vault_root=fixture.root,
                        db_path=fixture.fts5_db,
                    ),
                )
                scoped_paths: dict[str, set[str]] = {}
                for scope in ("alpha", "beta"):
                    rows = conn.execute(
                        "SELECT documents.path FROM documents_fts"
                        " JOIN documents ON documents.id = documents_fts.rowid"
                        " WHERE documents_fts MATCH ? AND documents.scope = ?",
                        (scope_token, scope),
                    ).fetchall()
                    scoped_paths[scope] = {str(row[0]) for row in rows}
                wildcard_rows = conn.execute(
                    "SELECT documents.path FROM documents_fts"
                    " JOIN documents ON documents.id = documents_fts.rowid"
                    " WHERE documents_fts MATCH ?",
                    (scope_token,),
                ).fetchall()
                wildcard_paths = {str(row[0]) for row in wildcard_rows}
                scope_passed = (
                    stats.indexed == 2
                    and scoped_paths == {
                        "alpha": {"alpha.md"},
                        "beta": {"beta.md"},
                    }
                    and wildcard_paths == {"alpha.md", "beta.md"}
                )
                checks.append(
                    result(
                        "isolation_scope",
                        scope_passed,
                        (
                            "two concrete scopes remained disjoint and the explicit "
                            "cross-scope view returned both fixture records"
                            if scope_passed
                            else "temporary FTS5 fixture violated the expected scope sets"
                        ),
                    )
                )

                stored_rows = conn.execute(
                    "SELECT documents.title, documents.title_normalized,"
                    " documents.content_raw, documents.body_text, documents.tags,"
                    " documents.frontmatter_type, documents.session_id,"
                    " documents.scope, documents.linked_notes, documents.key_points,"
                    " documents_fts.title, documents_fts.content,"
                    " documents_fts.tags, documents_fts.linked_notes"
                    " FROM documents JOIN documents_fts"
                    " ON documents.id = documents_fts.rowid"
                ).fetchall()
                stored_text = "\n".join(
                    str(value) for row in stored_rows for value in row
                )
            finally:
                conn.close()

            database_bytes = b"".join(
                path.read_bytes()
                for path in fixture.state_dir.glob("fts5.sqlite*")
                if path.is_file()
            )
            source_contains_secrets = all(
                secret in alpha_path.read_text(encoding="utf-8")
                or secret in beta_path.read_text(encoding="utf-8")
                for secret in scope_secrets
            )
            storage_passed = (
                source_contains_secrets
                and "[REDACTED]" in stored_text
                and all(secret not in stored_text for secret in scope_secrets)
                and all(secret.encode() not in database_bytes for secret in scope_secrets)
            )
            checks.append(
                result(
                    "isolation_storage_redaction",
                    storage_passed,
                    (
                        "private fixture values were absent from SQLite and FTS5 sinks"
                        if storage_passed
                        else "a private fixture value reached a derived storage sink"
                    ),
                )
            )
        except Exception as exc:  # noqa: BLE001 - report a bounded self-test failure
            existing = {entry["name"] for entry in checks}
            detail = f"temporary index verification raised {type(exc).__name__}"
            for name in ("isolation_scope", "isolation_storage_redaction"):
                if name not in existing:
                    checks.append(result(name, False, detail))

        try:
            enabled = CompressionConfig(enabled=True, cost_cap_usd_monthly=1.0)
            pipeline = pipeline_config_from_vault(fixture, enabled)
            prompt_path = fixture.root / "isolation-prompt.md"
            prompt_path.write_text(
                "Do not retain " f"<private>{prompt_secret}</private>.",
                encoding="utf-8",
            )
            pipeline = replace(
                pipeline,
                prompt_path=prompt_path,
                staging_active_window_s=-3600.0,
            )
            staging_path = pipeline.staging_dir / pipeline.host / "fixture.jsonl"
            staging_path.parent.mkdir(parents=True, exist_ok=True)
            staging_path.write_text(
                json.dumps(
                    {
                        "tool_name": "doctor-isolation",
                        "payload": (
                            "before "
                            f"<private>{provider_input_secret}</private> after"
                        ),
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            provider = IsolationProvider()
            report = run_compression(pipeline, provider)
            outbound = ""
            if provider.last_spec is not None:
                outbound = provider.last_spec.system + provider.last_spec.payload
            written_path = Path(report.written_path) if report.written_path else None
            persisted = (
                written_path.read_text(encoding="utf-8")
                if written_path is not None and written_path.is_file()
                else ""
            )
            written_inside_fixture = (
                written_path is not None
                and written_path.resolve().is_relative_to(fixture.root.resolve())
            )
            provider_passed = (
                report.status == "ok"
                and provider.last_spec is not None
                and written_inside_fixture
                and "[REDACTED]" in outbound
                and provider_input_secret not in outbound
                and prompt_secret not in outbound
                and "[REDACTED]" in persisted
                and provider_output_secret not in persisted
            )
            checks.append(
                result(
                    "isolation_provider_redaction",
                    provider_passed,
                    (
                        "provider input and provider output were redacted at both boundaries"
                        if provider_passed
                        else "temporary provider spy observed a redaction boundary failure"
                    ),
                )
            )
        except Exception as exc:  # noqa: BLE001 - report a bounded self-test failure
            checks.append(
                result(
                    "isolation_provider_redaction",
                    False,
                    f"temporary provider verification raised {type(exc).__name__}",
                )
            )

    return checks


def verify_isolation_boundaries() -> list[dict[str, str]]:
    """Run the disposable scope and redaction boundary verification."""
    return _verify_isolation_boundaries()


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
@click.option(
    "--verify-isolation",
    is_flag=True,
    help="Run scope and redaction self-tests in a disposable vault fixture.",
)
def doctor(
    vault_root: Path | None,
    as_json: bool,
    verify_isolation: bool,
) -> None:
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

    # --- 7. privacy_index ---
    # Verify that no <private> tag survived redaction into content_raw or body_text.
    # A nonzero count is a P3-class violation.
    if db_exists:
        try:
            conn = _sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
            try:
                row = conn.execute(
                    "SELECT COUNT(*) FROM documents"
                    " WHERE instr(content_raw, '<private') > 0"
                    " OR instr(body_text, '<private') > 0"
                ).fetchone()
                count = row[0] if row else 0
            finally:
                conn.close()
            if count > 0:
                checks.append(
                    _check(
                        "privacy_index",
                        "fail",
                        f"{count} row(s) contain raw <private> tag in content_raw or body_text"
                        " — P3-class violation; rebuild index",
                    )
                )
            else:
                checks.append(_check("privacy_index", "ok", "no raw <private> tags in index"))
        except _sqlite3.OperationalError as exc:
            checks.append(_check("privacy_index", "na", str(exc)))
        except Exception as exc:  # noqa: BLE001
            checks.append(_check("privacy_index", "warn", str(exc)))
    else:
        checks.append(_check("privacy_index", "na", "index absent — skipped"))

    # --- 8. locale_profile ---
    # Verify that a normalization_profile is recorded in index_meta and holds
    # a known value. An absent table is a legacy index (na). An absent key is
    # a warning. An unrecognised value is also a warning.
    # Must stay in step with ``fts5.indexer._NORMALIZER_PROFILE``. 'en-unicode'
    # was registered there in 4.0 but omitted here, so a correctly-built
    # English index was reported as an unexpected value by its own doctor.
    _KNOWN_PROFILES = frozenset({"tr-cldr", "tr-ascii-fold", "en-unicode", "identity"})
    if db_exists:
        try:
            conn = _sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
            try:
                row = conn.execute(
                    "SELECT value FROM index_meta WHERE key='normalization_profile'"
                ).fetchone()
            finally:
                conn.close()
            if row is None:
                checks.append(
                    _check(
                        "locale_profile",
                        "warn",
                        "normalization_profile absent (legacy index)"
                        " — rebuild to record profile",
                    )
                )
            elif row[0] not in _KNOWN_PROFILES:
                checks.append(
                    _check(
                        "locale_profile",
                        "warn",
                        f"unexpected normalization_profile value: {row[0]!r}"
                        f" — expected one of {sorted(_KNOWN_PROFILES)}",
                    )
                )
            else:
                checks.append(
                    _check(
                        "locale_profile",
                        "ok",
                        f"normalization_profile={row[0]!r}",
                    )
                )
        except _sqlite3.OperationalError:
            checks.append(_check("locale_profile", "na", "index_meta table absent (legacy index)"))
        except Exception as exc:  # noqa: BLE001
            checks.append(_check("locale_profile", "warn", str(exc)))
    else:
        checks.append(_check("locale_profile", "na", "index absent — skipped"))

    # --- 9. type_parity ---
    # Compare KNOWN_TYPES in vault.frontmatter against canonical_memory_types.json.
    # A mismatch means the fixture and the runtime frozenset have drifted.
    try:
        _fixture_path = (
            Path(__file__).parent.parent.parent
            / "tests" / "fixtures" / "canonical_memory_types.json"
        )
        _fixture_types: frozenset[str]
        if _fixture_path.exists():
            _raw = json.loads(_fixture_path.read_text(encoding="utf-8"))
            _fixture_types = frozenset(entry["type"] for entry in _raw)
        else:
            # Installed package: fixture not present; skip parity check gracefully.
            raise FileNotFoundError(f"fixture not found: {_fixture_path}")

        from .vault.frontmatter import KNOWN_TYPES as _KNOWN_TYPES
        if _fixture_types == _KNOWN_TYPES:
            checks.append(
                _check(
                    "type_parity",
                    "ok",
                    f"{len(_KNOWN_TYPES)} types match canonical_memory_types.json",
                )
            )
        else:
            only_fixture = _fixture_types - _KNOWN_TYPES
            only_code = _KNOWN_TYPES - _fixture_types
            checks.append(
                _check(
                    "type_parity",
                    "fail",
                    f"drift detected — only in fixture: {sorted(only_fixture)};"
                    f" only in KNOWN_TYPES: {sorted(only_code)}",
                )
            )
    except FileNotFoundError:
        # Fixture not shipped in installed packages — parity check is not
        # applicable; this is not an error condition for deployed users.
        checks.append(_check("type_parity", "na", "fixture not present (installed package)"))
    except Exception as exc:  # noqa: BLE001
        checks.append(_check("type_parity", "warn", str(exc)))

    # --- 10. content_hash_coverage ---
    # Spot-check that every documents row carries a non-NULL content_hash.
    # NULL rows indicate a pre-Phase-1 index that needs a rebuild.
    if db_exists:
        try:
            conn = _sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
            try:
                row = conn.execute(
                    "SELECT COUNT(*) FROM documents WHERE content_hash IS NULL"
                ).fetchone()
                null_count = row[0] if row else 0
            finally:
                conn.close()
            if null_count > 0:
                checks.append(
                    _check(
                        "content_hash_coverage",
                        "warn",
                        f"{null_count} row(s) missing content_hash; rebuild index",
                    )
                )
            else:
                checks.append(
                    _check("content_hash_coverage", "ok", "all rows have content_hash")
                )
        except _sqlite3.OperationalError:
            checks.append(
                _check(
                    "content_hash_coverage",
                    "na",
                    "content_hash column absent (pre-Phase-1 schema); rebuild index",
                )
            )
        except Exception as exc:  # noqa: BLE001
            checks.append(_check("content_hash_coverage", "warn", str(exc)))
    else:
        checks.append(_check("content_hash_coverage", "na", "index absent — skipped"))

    # --- 11. temporal_index ---
    # Check that the claims table exists and all rows carry content_hash and observed_at.
    # "na" when the table does not exist (temporal not yet indexed — not an error).
    if db_exists:
        try:
            conn = _sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
            try:
                table_row = conn.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' AND name='claims'"
                ).fetchone()
                if table_row is None:
                    checks.append(
                        _check(
                            "temporal_index",
                            "na",
                            "claims table absent — run: mneme-core temporal index",
                        )
                    )
                else:
                    count_row = conn.execute("SELECT COUNT(*) FROM claims").fetchone()
                    total = count_row[0] if count_row else 0
                    null_row = conn.execute(
                        "SELECT COUNT(*) FROM claims"
                        " WHERE content_hash IS NULL OR observed_at IS NULL"
                    ).fetchone()
                    null_count = null_row[0] if null_row else 0
                    if null_count > 0:
                        checks.append(
                            _check(
                                "temporal_index",
                                "warn",
                                f"{null_count} claim row(s) missing content_hash or"
                                f" observed_at (total={total}); rebuild: mneme-core temporal index",
                            )
                        )
                    else:
                        checks.append(
                            _check(
                                "temporal_index",
                                "ok",
                                f"claims={total}, all rows have content_hash and observed_at",
                            )
                        )
            finally:
                conn.close()
        except _sqlite3.OperationalError as exc:
            checks.append(_check("temporal_index", "na", str(exc)))
        except Exception as exc:  # noqa: BLE001
            checks.append(_check("temporal_index", "warn", str(exc)))
    else:
        checks.append(_check("temporal_index", "na", "index absent — skipped"))

    if verify_isolation:
        try:
            checks.extend(_verify_isolation_boundaries())
        except Exception as exc:  # noqa: BLE001 - keep doctor output structured
            checks.append(
                _check(
                    "isolation_fixture",
                    "fail",
                    f"temporary isolation fixture raised {type(exc).__name__}",
                )
            )

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


@cli.group("temporal", help="Temporal claim index subcommands.")
def temporal_group() -> None:  # pragma: no cover - dispatcher
    pass


def _resolve_temporal_scope(vault: VaultConfig, requested: str | None) -> str:
    from .scope import valid_scope

    candidate = requested if requested is not None else vault.default_scope()
    scope = valid_scope(candidate)
    if scope is None:
        raise click.ClickException("scope must be a valid identifier or '*'")
    return scope


@temporal_group.command("index", help="Build or rebuild the temporal claims index for the vault.")
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
    help="Token normalizer locale for statement_normalized.",
)
def temporal_index(vault_root: Path | None, locale: str) -> None:
    from .fts5.indexer import connect as fts5_connect
    from .temporal.index import ensure_temporal_schema, index_claims

    vault = _resolve_vault(vault_root)
    if locale == "tr":
        from .fts5.locale.tr import normalize_tr
        normalize = normalize_tr
    else:
        normalize = None

    conn = fts5_connect(vault.fts5_db)
    try:
        ensure_temporal_schema(conn)
        stats = index_claims(conn, vault, normalize=normalize)
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
                    "skipped_no_claim": stats.skipped_no_claim,
                    "skipped_error": stats.skipped_error,
                    "total_seen": stats.total_seen,
                },
            },
            indent=2,
            ensure_ascii=False,
        )
    )


@temporal_group.command("as-of", help="Print claims live at the given ISO timestamp.")
@click.argument("timestamp")
@click.option(
    "--vault",
    "vault_root",
    type=click.Path(file_okay=False, path_type=Path),
    default=None,
)
@click.option(
    "--scope",
    default=None,
    help="Concrete claim scope. Omit for the configured default, or pass '*' explicitly.",
)
def temporal_as_of(
    timestamp: str,
    vault_root: Path | None,
    scope: str | None,
) -> None:
    import sqlite3 as _sqlite3

    from .temporal.query import as_of as _as_of
    from .vault.frontmatter import _parse_dt

    t = _parse_dt(timestamp)
    if t is None:
        raise click.ClickException(f"Cannot parse timestamp: {timestamp!r}")

    vault = _resolve_vault(vault_root)
    resolved_scope = _resolve_temporal_scope(vault, scope)
    if not vault.fts5_db.exists():
        click.echo(json.dumps({"claims": [], "note": "index not found"}, indent=2))
        return

    conn = _sqlite3.connect(f"file:{vault.fts5_db}?mode=ro", uri=True)
    try:
        claims = _as_of(conn, t, scope=resolved_scope)
    finally:
        conn.close()

    payload = [
        {
            "claim_id": c.claim_id,
            "path": c.path,
            "statement": c.statement,
            "valid_from": c.valid_from.isoformat() if c.valid_from else None,
            "valid_to": c.valid_to.isoformat() if c.valid_to else None,
            "observed_at": c.observed_at.isoformat(),
            "claim_key": c.claim_key,
            "confidence_label": c.confidence_label.value,
            "scope": c.scope,
        }
        for c in claims
    ]
    click.echo(
        json.dumps(
            {
                "at": timestamp,
                "scope": resolved_scope,
                "count": len(payload),
                "claims": payload,
            },
            indent=2,
            ensure_ascii=False,
        )
    )


@temporal_group.command("current", help="Print claims live right now (UTC).")
@click.option(
    "--vault",
    "vault_root",
    type=click.Path(file_okay=False, path_type=Path),
    default=None,
)
@click.option(
    "--scope",
    default=None,
    help="Concrete claim scope. Omit for the configured default, or pass '*' explicitly.",
)
def temporal_current(vault_root: Path | None, scope: str | None) -> None:
    import sqlite3 as _sqlite3
    from datetime import UTC
    from datetime import datetime as _datetime

    from .temporal.query import current as _current

    vault = _resolve_vault(vault_root)
    resolved_scope = _resolve_temporal_scope(vault, scope)
    if not vault.fts5_db.exists():
        click.echo(json.dumps({"claims": [], "note": "index not found"}, indent=2))
        return

    now = _datetime.now(UTC)
    conn = _sqlite3.connect(f"file:{vault.fts5_db}?mode=ro", uri=True)
    try:
        claims = _current(conn, now, scope=resolved_scope)
    finally:
        conn.close()

    payload = [
        {
            "claim_id": c.claim_id,
            "path": c.path,
            "statement": c.statement,
            "valid_from": c.valid_from.isoformat() if c.valid_from else None,
            "valid_to": c.valid_to.isoformat() if c.valid_to else None,
            "observed_at": c.observed_at.isoformat(),
            "claim_key": c.claim_key,
            "confidence_label": c.confidence_label.value,
            "scope": c.scope,
        }
        for c in claims
    ]
    click.echo(
        json.dumps(
            {
                "at": now.isoformat(),
                "scope": resolved_scope,
                "count": len(payload),
                "claims": payload,
            },
            indent=2,
            ensure_ascii=False,
        )
    )


def _claim_payload(c: object) -> dict[str, object]:
    """Serialize a Claim for CLI JSON output (shared by blame/contradictions)."""
    from .temporal.claim import Claim

    assert isinstance(c, Claim)
    return {
        "claim_id": c.claim_id,
        "path": c.path,
        "statement": c.statement,
        "valid_from": c.valid_from.isoformat() if c.valid_from else None,
        "valid_to": c.valid_to.isoformat() if c.valid_to else None,
        "observed_at": c.observed_at.isoformat(),
        "supersedes": c.supersedes,
        "claim_key": c.claim_key,
        "confidence_label": c.confidence_label.value,
        "trust": c.trust,
        "content_hash": c.content_hash,
        "scope": c.scope,
    }


@temporal_group.command(
    "blame",
    help=(
        "git-blame for memories: show where a claim came from, what it "
        "supersedes, what superseded it, and its rivals. REF is a claim_id "
        "or a vault-relative markdown path."
    ),
)
@click.argument("ref")
@click.option(
    "--vault",
    "vault_root",
    type=click.Path(file_okay=False, path_type=Path),
    default=None,
)
@click.option(
    "--scope",
    default=None,
    help="Concrete claim scope. Omit for the configured default, or pass '*' explicitly.",
)
def temporal_blame(
    ref: str,
    vault_root: Path | None,
    scope: str | None,
) -> None:
    import sqlite3 as _sqlite3

    from .temporal.blame import blame as _blame

    vault = _resolve_vault(vault_root)
    resolved_scope = _resolve_temporal_scope(vault, scope)
    if not vault.fts5_db.exists():
        click.echo(json.dumps({"reports": [], "note": "index not found"}, indent=2))
        return

    conn = _sqlite3.connect(f"file:{vault.fts5_db}?mode=ro", uri=True)
    try:
        reports = _blame(conn, ref, scope=resolved_scope)
    finally:
        conn.close()

    payload = [
        {
            "target": _claim_payload(r.target),
            "ancestors": [_claim_payload(c) for c in r.ancestors],
            "descendants": [_claim_payload(c) for c in r.descendants],
            "rivals": [_claim_payload(c) for c in r.rivals],
        }
        for r in reports
    ]
    click.echo(
        json.dumps(
            {
                "ref": ref,
                "scope": resolved_scope,
                "count": len(payload),
                "reports": payload,
            },
            indent=2,
            ensure_ascii=False,
        )
    )


@temporal_group.command(
    "contradictions",
    help="List claim pairs sharing a claim_key with overlapping validity windows.",
)
@click.option(
    "--vault",
    "vault_root",
    type=click.Path(file_okay=False, path_type=Path),
    default=None,
)
@click.option(
    "--scope",
    default=None,
    help="Concrete claim scope. Omit for the configured default, or pass '*' explicitly.",
)
def temporal_contradictions(vault_root: Path | None, scope: str | None) -> None:
    import sqlite3 as _sqlite3

    from .temporal.blame import _claim_by_id
    from .temporal.query import find_contradictions_scoped as _find

    vault = _resolve_vault(vault_root)
    resolved_scope = _resolve_temporal_scope(vault, scope)
    if not vault.fts5_db.exists():
        click.echo(json.dumps({"contradictions": [], "note": "index not found"}, indent=2))
        return

    conn = _sqlite3.connect(f"file:{vault.fts5_db}?mode=ro", uri=True)
    try:
        pairs = _find(conn, scope=resolved_scope)
        payload = []
        for pair_scope, a_id, b_id in pairs:
            a = _claim_by_id(conn, a_id, pair_scope)
            b = _claim_by_id(conn, b_id, pair_scope)
            payload.append(
                {
                    "scope": pair_scope,
                    "a": _claim_payload(a) if a else {"claim_id": a_id},
                    "b": _claim_payload(b) if b else {"claim_id": b_id},
                }
            )
    finally:
        conn.close()
    click.echo(
        json.dumps(
            {
                "scope": resolved_scope,
                "count": len(payload),
                "contradictions": payload,
            },
            indent=2,
            ensure_ascii=False,
        )
    )


@cli.group(
    "memory",
    help="Policy-graduated autonomous memory edits: policy, changes, rollback, drain.",
)
def memory_group() -> None:  # pragma: no cover - dispatcher
    pass


@memory_group.group(
    "policy",
    invoke_without_command=True,
    help="Show, scaffold, or validate the autonomy policy.",
)
@click.option(
    "--vault",
    "vault_root",
    type=click.Path(file_okay=False, path_type=Path),
    default=None,
)
@click.pass_context
def memory_policy(ctx: click.Context, vault_root: Path | None) -> None:
    if ctx.invoked_subcommand is not None:
        return
    from .policy import POLICY_CONFIG_FILENAME, load_policy

    vault = _resolve_vault(vault_root)
    policy = load_policy(vault)
    click.echo(
        json.dumps(
            {
                "config_path": str(vault.state_dir / POLICY_CONFIG_FILENAME),
                "auto_approve": sorted(c.value for c in policy.allowed_classes),
                "clinical_lock": policy.clinical_lock,
            },
            indent=2,
            ensure_ascii=False,
        )
    )


@memory_policy.command(
    "init", help="Write a documented starter policy.json. Never overwrites."
)
@click.option(
    "--vault",
    "vault_root",
    type=click.Path(file_okay=False, path_type=Path),
    default=None,
)
def memory_policy_init(vault_root: Path | None) -> None:
    from .policy import POLICY_CONFIG_FILENAME, default_policy_payload

    vault = _resolve_vault(vault_root)
    path = vault.state_dir / POLICY_CONFIG_FILENAME
    if path.exists():
        click.echo(
            json.dumps(
                {
                    "created": False,
                    "config_path": str(path),
                    "note": "policy.json already exists; left untouched",
                },
                indent=2,
                ensure_ascii=False,
            )
        )
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(default_policy_payload(), indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    click.echo(
        json.dumps(
            {"created": True, "config_path": str(path)}, indent=2, ensure_ascii=False
        )
    )


@memory_policy.command(
    "validate", help="Validate policy.json and surface unknown class names."
)
@click.option(
    "--vault",
    "vault_root",
    type=click.Path(file_okay=False, path_type=Path),
    default=None,
)
def memory_policy_validate(vault_root: Path | None) -> None:
    from .policy import inspect_policy

    vault = _resolve_vault(vault_root)
    report = inspect_policy(vault)
    click.echo(json.dumps(report, indent=2, ensure_ascii=False))
    if not report.get("valid") or report.get("unknown_classes"):
        raise SystemExit(1)


@memory_group.command("changes", help="List applied autonomous edits (rollback journal).")
@click.option(
    "--vault",
    "vault_root",
    type=click.Path(file_okay=False, path_type=Path),
    default=None,
)
def memory_changes(vault_root: Path | None) -> None:
    from .memory_apply import list_changes

    vault = _resolve_vault(vault_root)
    changes = list_changes(vault)
    click.echo(
        json.dumps(
            {"count": len(changes), "changes": changes},
            indent=2,
            ensure_ascii=False,
        )
    )


@memory_group.command("rollback", help="Restore the prior state for one change id.")
@click.argument("change_id")
@click.option(
    "--vault",
    "vault_root",
    type=click.Path(file_okay=False, path_type=Path),
    default=None,
)
def memory_rollback(change_id: str, vault_root: Path | None) -> None:
    from .memory_apply import rollback_change

    vault = _resolve_vault(vault_root)
    result = rollback_change(vault, change_id)
    click.echo(
        json.dumps(
            {
                "applied": result.applied,
                "change_id": result.change_id,
                "reason": result.reason,
                "path": result.path,
            },
            indent=2,
            ensure_ascii=False,
        )
    )
    if not result.applied:
        sys.exit(1)


@memory_group.command(
    "drain", help="Apply queued memory proposals under the current policy."
)
@click.option(
    "--vault",
    "vault_root",
    type=click.Path(file_okay=False, path_type=Path),
    default=None,
)
def memory_drain(vault_root: Path | None) -> None:
    from .memory_apply import drain_proposals

    vault = _resolve_vault(vault_root)
    report = drain_proposals(vault)
    click.echo(
        json.dumps(
            {
                "applied": report.applied,
                "refused": report.refused,
                "malformed": report.malformed,
                "results": [
                    {
                        "applied": r.applied,
                        "change_id": r.change_id,
                        "reason": r.reason,
                        "path": r.path,
                    }
                    for r in report.results
                ],
            },
            indent=2,
            ensure_ascii=False,
        )
    )


@cli.group(
    "sync",
    help="Self-hosted team vault sync over plain git (redaction-before-share).",
)
def sync_group() -> None:  # pragma: no cover - dispatcher
    pass


@sync_group.command("status", help="Show the resolved sync configuration.")
@click.option(
    "--vault",
    "vault_root",
    type=click.Path(file_okay=False, path_type=Path),
    default=None,
)
def sync_status(vault_root: Path | None) -> None:
    from .sync import SYNC_CONFIG_FILENAME, load_sync_config

    vault = _resolve_vault(vault_root)
    config = load_sync_config(vault)
    click.echo(
        json.dumps(
            {
                "config_path": str(vault.state_dir / SYNC_CONFIG_FILENAME),
                "configured": config.configured,
                "remote_url": config.remote_url,
                "branch": config.branch,
                "member": config.member,
                "exclude": list(config.exclude),
                "encrypted": bool(config.encrypt_recipients),
            },
            indent=2,
            ensure_ascii=False,
        )
    )


@sync_group.command(
    "push", help="Build the redacted share tree and push it to the team remote."
)
@click.option(
    "--vault",
    "vault_root",
    type=click.Path(file_okay=False, path_type=Path),
    default=None,
)
def sync_push(vault_root: Path | None) -> None:
    from .sync import push as _push

    vault = _resolve_vault(vault_root)
    result = _push(vault)
    payload: dict[str, object] = {"ok": result.ok, "detail": result.detail}
    if result.report is not None:
        payload["report"] = {
            "files_shared": result.report.files_shared,
            "files_excluded": result.report.files_excluded,
            "redactions_applied": result.report.redactions_applied,
            "leaked_paths": list(result.report.leaked_paths),
        }
    click.echo(json.dumps(payload, indent=2, ensure_ascii=False))
    if not result.ok:
        sys.exit(1)


@sync_group.command(
    "pull", help="Fetch teammates' share trees and import them under team/."
)
@click.option(
    "--vault",
    "vault_root",
    type=click.Path(file_okay=False, path_type=Path),
    default=None,
)
def sync_pull(vault_root: Path | None) -> None:
    from .sync import pull as _pull

    vault = _resolve_vault(vault_root)
    result = _pull(vault)
    click.echo(
        json.dumps(
            {
                "ok": result.ok,
                "detail": result.detail,
                "imported": list(result.imported),
                "conflicts": list(result.conflicts),
            },
            indent=2,
            ensure_ascii=False,
        )
    )
    if not result.ok:
        sys.exit(1)


@cli.command("version", help="Print mneme-core version.")
def version_cmd() -> None:
    click.echo(__version__)


def main() -> None:
    cli(prog_name="mneme-core")


if __name__ == "__main__":  # pragma: no cover
    main()
