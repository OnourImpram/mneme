"""Command-line interface for mneme-graph.

Subcommands
-----------
build [--vault VAULT_ROOT]
    Walk all supported source files (.py, .js, .jsx, .mjs, .cjs, .ts, .tsx,
    .md, .markdown) under the vault root (respecting the extractor's
    symlink-escape containment), extract nodes+edges, and save graph.json
    under <vault>/.mneme/graph.json.  Prints a one-line summary on stdout.
    Idempotent: running twice on unchanged source produces identical output.

report [--vault VAULT_ROOT]
    Load graph.json and print:
    - Node counts by kind
    - Edge counts by kind
    - Top-10 most-referenced nodes by in-degree
    - External dependency list (nodes with source_path='<external>')
    Deterministic; no network access.

No new dependencies: argparse only (stdlib).

Security: symlink-escape containment is enforced by the extractor itself.

Redaction: graph.json nodes contain only code identifiers, symbol names,
heading text, tag names, and vault-relative paths; no file content or
string-literal prose is stored.
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter
from pathlib import Path

from .extractor import SUPPORTED_SUFFIXES, extract_any
from .schema import GraphNode
from .store import GraphStore


def run_build(vault_root: Path) -> dict[str, int]:
    """Build graph.json from all supported source files under vault_root.

    Walks the vault using rglob for each suffix in SUPPORTED_SUFFIXES,
    respects the extractor's symlink-escape guard (files that escape the
    vault are skipped with a warning rather than crashing the full build).

    Returns a summary dict with keys 'nodes' and 'edges' (total counts
    after deduplication).
    """
    store = GraphStore(vault_root)
    store.clear()

    # Collect all matching paths across all supported suffixes, then deduplicate
    # (multi-suffix rglob can visit the same path twice on case-insensitive or
    # symlink-rich filesystems) and sort for deterministic build order.
    all_paths: list[Path] = []
    for suffix in SUPPORTED_SUFFIXES:
        all_paths.extend(vault_root.rglob(f"*{suffix}"))

    for src_path in sorted(set(all_paths)):
        try:
            nodes, edges = extract_any(src_path, vault_root)
        except ValueError:
            # Symlink escapes vault — skip silently (per security invariant).
            continue
        except OSError as exc:
            print(f"warning: could not read {src_path}: {exc}", file=sys.stderr)
            continue
        store.add_nodes(nodes)
        store.add_edges(edges)

    store.save()
    return {"nodes": len(store.nodes), "edges": len(store.edges)}


def run_report(vault_root: Path, top_n: int = 10) -> dict[str, object]:
    """Load graph.json and return a structured report dict.

    Keys:
        node_counts     dict[kind, count]
        edge_counts     dict[kind, count]
        top_referenced  list of (name, in_degree) tuples, descending
        external_deps   sorted list of external dependency names
    """
    store = GraphStore.load(vault_root)
    nodes = store.nodes
    edges = store.edges

    if not nodes:
        return {
            "node_counts": {},
            "edge_counts": {},
            "top_referenced": [],
            "external_deps": [],
        }

    node_counts: dict[str, int] = Counter(n.kind for n in nodes)
    edge_counts: dict[str, int] = Counter(e.kind for e in edges)

    # In-degree: how many edges point *into* each node.
    in_degree: Counter[str] = Counter()
    for edge in edges:
        in_degree[edge.dst_id] += 1

    node_by_id: dict[str, GraphNode] = {n.node_id: n for n in nodes}
    top_referenced: list[tuple[str, int]] = [
        (node_by_id[nid].name, cnt)
        for nid, cnt in in_degree.most_common(top_n)
        if nid in node_by_id
    ]

    external_deps: list[str] = sorted({n.name for n in nodes if n.source_path == "<external>"})

    return {
        "node_counts": dict(node_counts),
        "edge_counts": dict(edge_counts),
        "top_referenced": top_referenced,
        "external_deps": external_deps,
    }


def _print_report(report: dict[str, object], vault_root: Path) -> None:
    """Pretty-print a report dict to stdout."""
    print(f"\nGraph report for vault: {vault_root}\n")

    node_counts = report.get("node_counts", {})
    assert isinstance(node_counts, dict)
    print("Node counts by kind:")
    if node_counts:
        for kind, cnt in sorted(node_counts.items()):
            print(f"  {kind:<12} {cnt}")
    else:
        print("  (no nodes — run `mneme-graph build` first)")

    edge_counts = report.get("edge_counts", {})
    assert isinstance(edge_counts, dict)
    print("\nEdge counts by kind:")
    if edge_counts:
        for kind, cnt in sorted(edge_counts.items()):
            print(f"  {kind:<12} {cnt}")
    else:
        print("  (no edges)")

    top_referenced = report.get("top_referenced", [])
    assert isinstance(top_referenced, list)
    print("\nTop most-referenced nodes (by in-degree):")
    if top_referenced:
        for name, deg in top_referenced:
            print(f"  {name:<40} in-degree={deg}")
    else:
        print("  (none)")

    external_deps = report.get("external_deps", [])
    assert isinstance(external_deps, list)
    print("\nExternal dependencies:")
    if external_deps:
        for dep in external_deps:
            print(f"  {dep}")
    else:
        print("  (none)")
    print()


def run_impact(
    vault_root: Path,
    changed_files: list[str],
    *,
    max_depth: int | None = None,
) -> dict[str, object]:
    """PR-impact: which nodes depend (transitively) on the changed files.

    *changed_files* are vault-relative paths (posix or os separators).
    Every node whose ``source_path`` matches a changed file seeds the
    reverse-BFS in :func:`mneme_graph.analytics.pr_impact`.

    Returns a JSON-ready dict: the changed files that matched at least
    one node, the seed node count, and the affected nodes (name, kind,
    source_path, hop distance), sorted by distance then id.
    """
    from .analytics import apply_merge, changed_nodes_for_paths, pr_impact

    store = GraphStore.load(vault_root)
    nodes = store.nodes
    edges = store.edges
    # Resolve external ghost nodes onto their unambiguous local definition
    # (PA3 entity canonicalization, impact-time only — graph.json on disk
    # stays exactly as built). Without this, a cross-file call lands on an
    # `<external>` ghost and the reverse BFS never reaches the real caller.
    canonical = _external_resolution_map(nodes)
    if canonical:
        nodes, edges = apply_merge(nodes, edges, canonical)
    normalized = {f.replace("\\", "/").strip() for f in changed_files if f.strip()}
    seed_ids = changed_nodes_for_paths(nodes, normalized)
    matched_files = sorted({n.source_path for n in nodes if n.source_path in normalized})
    result = pr_impact(nodes, edges, seed_ids, max_depth=max_depth)
    node_by_id = {n.node_id: n for n in nodes}
    affected = [
        {
            "name": node_by_id[nid].name,
            "kind": node_by_id[nid].kind,
            "source_path": node_by_id[nid].source_path,
            "distance": dist,
        }
        for nid, dist in result.affected
        if nid in node_by_id
    ]
    affected_files = sorted(
        {str(a["source_path"]) for a in affected if a["source_path"] != "<external>"}
    )
    return {
        "changed_files": matched_files,
        "unmatched_files": sorted(normalized - set(matched_files)),
        "seed_nodes": len(result.changed),
        "affected_nodes": affected,
        "affected_files": affected_files,
    }


def _external_resolution_map(nodes: list[GraphNode]) -> dict[str, str]:
    """Map external ghost node_ids to their unique local definition.

    A ghost ``(name, kind, source_path='<external>')`` resolves to a local
    node when exactly ONE local node shares its ``(name, kind)``. Modules
    additionally match on the last dotted segment (``src.alpha`` → local
    module ``alpha``). Ambiguous names (two local defs) stay unresolved —
    fail open to "no merge", never guess.
    """
    local_by_key: dict[tuple[str, str], list[str]] = {}
    for node in nodes:
        if node.source_path == "<external>":
            continue
        local_by_key.setdefault((node.name, node.kind), []).append(node.node_id)

    mapping: dict[str, str] = {}
    for node in nodes:
        if node.source_path != "<external>":
            continue
        candidates = local_by_key.get((node.name, node.kind), [])
        if not candidates and node.kind == "module" and "." in node.name:
            tail = node.name.rsplit(".", 1)[-1]
            candidates = local_by_key.get((tail, "module"), [])
        if len(candidates) == 1:
            mapping[node.node_id] = candidates[0]
    return mapping


def _git_changed_files(vault_root: Path) -> list[str]:
    """Changed + untracked files from git, vault-relative. Empty on failure."""
    import subprocess

    files: list[str] = []
    for cmd in (
        ["git", "diff", "--name-only", "HEAD"],
        ["git", "ls-files", "--others", "--exclude-standard"],
    ):
        try:
            proc = subprocess.run(
                cmd,
                cwd=str(vault_root),
                capture_output=True,
                text=True,
                encoding="utf-8",
                timeout=10,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            return files
        if proc.returncode == 0:
            files.extend(line.strip() for line in proc.stdout.splitlines() if line.strip())
    return files


def main() -> None:
    """Entry point for the mneme-graph console script."""
    parser = argparse.ArgumentParser(
        prog="mneme-graph",
        description="mneme-graph: build and inspect a local code knowledge graph.",
    )
    parser.add_argument(
        "--vault",
        metavar="VAULT_ROOT",
        default=".",
        help="Path to the vault root directory (default: current directory).",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    # build subcommand
    subparsers.add_parser(
        "build",
        help="Extract nodes+edges from all supported files and write graph.json.",
    )

    # report subcommand
    report_parser = subparsers.add_parser(
        "report",
        help="Load graph.json and print node/edge counts and top-referenced nodes.",
    )
    report_parser.add_argument(
        "--top",
        metavar="N",
        type=int,
        default=10,
        help="Number of top-referenced nodes to show (default: 10).",
    )

    # impact subcommand
    impact_parser = subparsers.add_parser(
        "impact",
        help=(
            "PR-impact analysis: list nodes and files that transitively "
            "depend on the changed files (reverse BFS over the graph)."
        ),
    )
    impact_parser.add_argument(
        "paths",
        nargs="*",
        metavar="PATH",
        help="Changed files, vault-relative. Omit with --diff to use git.",
    )
    impact_parser.add_argument(
        "--diff",
        action="store_true",
        help="Seed from `git diff --name-only HEAD` plus untracked files.",
    )
    impact_parser.add_argument(
        "--max-depth",
        metavar="N",
        type=int,
        default=None,
        help="Maximum hop distance to explore (default: unbounded).",
    )

    args = parser.parse_args()

    # --vault is a top-level option only (no subcommand defines it); argparse
    # always populates args.vault from the top-level default (".") or the value
    # the user passed before the subcommand.
    vault_root = Path(args.vault).resolve()

    if args.command == "build":
        summary = run_build(vault_root)
        print(
            f"graph built: {summary['nodes']} nodes, {summary['edges']} edges"
            f" -> {vault_root / '.mneme' / 'graph.json'}"
        )

    elif args.command == "report":
        report = run_report(vault_root, top_n=args.top)
        _print_report(report, vault_root)

    elif args.command == "impact":
        import json as _json

        changed: list[str] = list(args.paths)
        if args.diff:
            changed.extend(_git_changed_files(vault_root))
        if not changed:
            print(
                "mneme-graph impact: no changed files (pass paths or --diff)",
                file=sys.stderr,
            )
            raise SystemExit(1)
        impact = run_impact(vault_root, changed, max_depth=args.max_depth)
        print(_json.dumps(impact, indent=2, ensure_ascii=False))
