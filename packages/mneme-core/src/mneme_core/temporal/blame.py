"""Memory blame: provenance time-travel over the claims table.

``git blame`` answers "who wrote this line and when". ``mneme blame``
answers the memory-layer equivalent: where did this piece of knowledge
come from, when was it valid, what replaced it, and what does it
replace. The walk is pure SQLite over the derived ``claims`` table
(deterministic, zero-LLM, rebuildable), so a blame report can always be
re-derived from vault markdown.

A blame target is either a ``claim_id`` or a vault-relative markdown
path. Paths may carry several claims; the public entry point therefore
returns a list of reports.

Lineage semantics:

* **ancestors** — the chain this claim supersedes, oldest last:
  target ``supersedes`` A, A ``supersedes`` B → ``[A, B]``.
* **descendants** — claims that (transitively) supersede the target,
  newest last.
* **rivals** — other claims sharing the target's non-null
  ``claim_key``; the contradiction context an auditor wants in view.

Both chain walks carry a visited-set so a malformed self-referential
or cyclic ``supersedes`` graph in user frontmatter terminates cleanly
instead of looping.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field

from .claim import Claim
from .query import _all_columns, _row_to_claim, _table_exists

_MAX_CHAIN = 64  # hard bound against degenerate supersession graphs


@dataclass(frozen=True)
class BlameReport:
    """Full lineage view for one claim."""

    target: Claim
    ancestors: list[Claim] = field(default_factory=list)
    descendants: list[Claim] = field(default_factory=list)
    rivals: list[Claim] = field(default_factory=list)


def _claim_by_id(
    conn: sqlite3.Connection,
    claim_id: str,
    scope: str = "default",
) -> Claim | None:
    row = conn.execute(
        f"SELECT {_all_columns()} FROM claims "  # noqa: S608
        "WHERE claim_id = ? AND scope = ?",
        (claim_id, scope),
    ).fetchone()
    return _row_to_claim(row) if row is not None else None


def _claims_by_path(conn: sqlite3.Connection, path: str, scope: str) -> list[Claim]:
    scope_sql = "" if scope == "*" else "AND scope = ?"
    params = (path,) if scope == "*" else (path, scope)
    rows = conn.execute(
        f"SELECT {_all_columns()} FROM claims WHERE path = ? "  # noqa: S608
        f"{scope_sql} ORDER BY observed_at, scope, claim_id",
        params,
    ).fetchall()
    return [_row_to_claim(r) for r in rows]


def _walk_ancestors(conn: sqlite3.Connection, start: Claim) -> list[Claim]:
    chain: list[Claim] = []
    visited = {start.claim_id}
    cursor = start.supersedes
    while cursor and len(chain) < _MAX_CHAIN:
        if cursor in visited:
            break
        visited.add(cursor)
        prev = _claim_by_id(conn, cursor, start.scope)
        if prev is None:
            break
        chain.append(prev)
        cursor = prev.supersedes
    return chain


def _walk_descendants(conn: sqlite3.Connection, start: Claim) -> list[Claim]:
    chain: list[Claim] = []
    visited = {start.claim_id}
    queue = [start.claim_id]
    while queue and len(chain) < _MAX_CHAIN:
        cursor = queue.pop(0)
        rows = conn.execute(
            f"SELECT {_all_columns()} FROM claims WHERE supersedes = ? "  # noqa: S608
            "AND scope = ? "
            "ORDER BY observed_at, claim_id",
            (cursor, start.scope),
        ).fetchall()
        for row in rows:
            if len(chain) >= _MAX_CHAIN:
                break
            nxt = _row_to_claim(row)
            if nxt.claim_id in visited:
                continue
            visited.add(nxt.claim_id)
            chain.append(nxt)
            queue.append(nxt.claim_id)
    return chain


def _rivals(conn: sqlite3.Connection, target: Claim) -> list[Claim]:
    if not target.claim_key:
        return []
    rows = conn.execute(
        f"SELECT {_all_columns()} FROM claims "  # noqa: S608
        "WHERE claim_key = ? AND claim_id != ? AND scope = ? "
        "ORDER BY observed_at, claim_id",
        (target.claim_key, target.claim_id, target.scope),
    ).fetchall()
    return [_row_to_claim(r) for r in rows]


def blame(
    conn: sqlite3.Connection,
    ref: str,
    *,
    scope: str = "default",
) -> list[BlameReport]:
    """Resolve *ref* (claim_id or vault-relative path) into blame reports.

    Returns an empty list when the claims table is absent or the ref
    matches nothing. Never raises on a well-formed connection.
    """
    if not _table_exists(conn):
        return []
    targets: list[Claim]
    if scope == "*":
        rows = conn.execute(
            f"SELECT {_all_columns()} FROM claims "  # noqa: S608
            "WHERE claim_id = ? ORDER BY scope",
            (ref,),
        ).fetchall()
        targets = [_row_to_claim(row) for row in rows]
    else:
        by_id = _claim_by_id(conn, ref, scope)
        targets = [by_id] if by_id is not None else []
    if not targets:
        normalized = ref.replace("\\", "/").strip()
        targets = _claims_by_path(conn, normalized, scope)
    return [
        BlameReport(
            target=t,
            ancestors=_walk_ancestors(conn, t),
            descendants=_walk_descendants(conn, t),
            rivals=_rivals(conn, t),
        )
        for t in targets
    ]
