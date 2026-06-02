"""Command-line interface for mneme-graph.

Subcommands
-----------
build [--vault VAULT_ROOT]
    Walk all .py files under the vault root (respecting the extractor's
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

Redaction: graph.json nodes contain only code identifiers (symbol names) and
vault-relative paths; no file content or string-literal values are stored.
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
        help="Extract nodes+edges from all .py files and write graph.json.",
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
