"""Temporal claim indexer: schema creation and vault walk.

The ``claims`` table lives in the same SQLite file as the FTS5 index
(``vault.fts5_db``).  It is fully rebuildable from vault markdown; deleting
it and running ``index_claims`` restores all data.

Re-indexing is idempotent: every upsert uses ``ON CONFLICT(claim_id) DO UPDATE``
so running twice leaves row counts unchanged.

Supersession pass
-----------------
After all claims are upserted, the indexer updates ``superseded_by`` on each
target claim to record the latest claim that supersedes it.  This is an
informational denormalization: :func:`~mneme_core.temporal.query.as_of` computes
supersession dynamically and never relies on this column for correctness.
"""

from __future__ import annotations

import hashlib
import re
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ..privacy import redact
from ..vault.frontmatter import load_yaml_block
from .claim import _to_utc_iso, claim_from_note

_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)

_DEFAULT_EXCLUDE: tuple[str, ...] = (
    "/.git/",
    "/node_modules/",
    "/.claude/",
    "/.obsidian/",
    "/.trash/",
    "/.idea/",
    "/__pycache__/",
    "/.mneme/",
)


@dataclass
class ClaimIndexStats:
    """Counters returned by a single :func:`index_claims` pass."""

    indexed: int = 0
    skipped_no_claim: int = 0
    skipped_error: int = 0
    total_seen: int = 0


def ensure_temporal_schema(conn: sqlite3.Connection) -> None:
    """Create the ``claims`` table and supporting indexes if they do not exist.

    Safe to call on a database that already has the table; all DDL statements
    are ``IF NOT EXISTS``.
    """
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS claims (
            claim_id          TEXT PRIMARY KEY,
            path              TEXT NOT NULL,
            statement         TEXT NOT NULL,
            statement_normalized TEXT NOT NULL,
            valid_from        TEXT,
            valid_to          TEXT,
            observed_at       TEXT NOT NULL,
            supersedes        TEXT,
            superseded_by     TEXT,
            claim_key         TEXT,
            confidence_label  TEXT NOT NULL,
            trust             TEXT NOT NULL,
            content_hash      TEXT NOT NULL,
            indexed_at        TEXT NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_claims_claim_key
            ON claims(claim_key);

        CREATE INDEX IF NOT EXISTS idx_claims_validity
            ON claims(valid_from, valid_to);
        """
    )


def _parse_frontmatter(content: str) -> tuple[dict[str, Any], str]:
    """Parse frontmatter from already-redacted content. Returns ({}, body) on failure."""
    import yaml

    m = _FRONTMATTER_RE.match(content)
    if not m:
        return {}, content
    body = content[m.end():]
    try:
        data = load_yaml_block(m.group(1))
    except yaml.YAMLError:
        return {}, body
    if not isinstance(data, dict):
        return {}, body
    return data, body


def _should_index(path: Path, vault_root: Path, exclude: tuple[str, ...]) -> bool:
    s = "/" + str(path.relative_to(vault_root)).replace("\\", "/") + "/"
    return not any(p in s for p in exclude)


def _dt_to_iso(dt: datetime | None) -> str | None:
    """Return a UTC-normalised ISO string for storage, or None.

    Delegates to :func:`~mneme_core.temporal.claim._to_utc_iso` so storage
    and query are guaranteed to produce the same canonical form.
    """
    return _to_utc_iso(dt) if dt is not None else None


def index_claims(
    conn: sqlite3.Connection,
    vault: Any,
    *,
    normalize: Any = None,
) -> ClaimIndexStats:
    """Walk the vault and upsert every claim note into the ``claims`` table.

    Parameters
    ----------
    conn:
        Open SQLite connection. ``ensure_temporal_schema`` must have been called.
    vault:
        A :class:`~mneme_core.vault.config.VaultConfig` instance.
    normalize:
        Optional ``(str) -> str`` normalizer applied to ``statement_normalized``.
        Defaults to identity.

    Returns
    -------
    ClaimIndexStats
        Counters for indexed, skipped, and total-seen files.

    Notes
    -----
    Only markdown files that produce a non-None :func:`claim_from_note` result
    are inserted.  Non-claim notes are skipped without error.

    The supersession pass at the end sets ``superseded_by`` on each target row
    (informational only; never relied upon by ``as_of``).
    """
    stats = ClaimIndexStats()
    vault_root: Path = vault.root
    vault_root_resolved = vault_root.resolve()
    now_iso = datetime.now(UTC).isoformat()

    paths = sorted(vault_root.rglob("*.md"))
    stats.total_seen = len(paths)

    for md_path in paths:
        if not _should_index(md_path, vault_root, _DEFAULT_EXCLUDE):
            stats.skipped_no_claim += 1
            continue

        try:
            resolved = md_path.resolve()
        except OSError:
            stats.skipped_error += 1
            continue
        if not resolved.is_relative_to(vault_root_resolved):
            stats.skipped_no_claim += 1
            continue

        rel_path = str(md_path.relative_to(vault_root)).replace("\\", "/")

        try:
            raw_bytes = md_path.read_bytes()
            content = redact(raw_bytes.decode("utf-8", errors="replace"))
            content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
        except OSError:
            stats.skipped_error += 1
            continue

        fm, body = _parse_frontmatter(content)
        claim = claim_from_note(
            fm,
            body,
            path=rel_path,
            content_hash=content_hash,
            normalize=normalize,
        )
        if claim is None:
            stats.skipped_no_claim += 1
            continue

        conn.execute(
            """
            INSERT INTO claims
                (claim_id, path, statement, statement_normalized,
                 valid_from, valid_to, observed_at,
                 supersedes, superseded_by, claim_key,
                 confidence_label, trust, content_hash, indexed_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, ?, ?, ?, ?)
            ON CONFLICT(claim_id) DO UPDATE SET
                path=excluded.path,
                statement=excluded.statement,
                statement_normalized=excluded.statement_normalized,
                valid_from=excluded.valid_from,
                valid_to=excluded.valid_to,
                observed_at=excluded.observed_at,
                supersedes=excluded.supersedes,
                claim_key=excluded.claim_key,
                confidence_label=excluded.confidence_label,
                trust=excluded.trust,
                content_hash=excluded.content_hash,
                indexed_at=excluded.indexed_at
            """,
            (
                claim.claim_id,
                claim.path,
                claim.statement,
                claim.statement_normalized,
                _dt_to_iso(claim.valid_from),
                _dt_to_iso(claim.valid_to),
                _to_utc_iso(claim.observed_at),
                claim.supersedes,
                claim.claim_key,
                claim.confidence_label.value,
                claim.trust,
                claim.content_hash,
                now_iso,
            ),
        )
        stats.indexed += 1

    # --- Supersession pass ---
    # For every claim with supersedes set, update the target's superseded_by.
    # Never deletes the target row; never mutates the target's core fields.
    rows = conn.execute(
        "SELECT claim_id, supersedes FROM claims WHERE supersedes IS NOT NULL"
    ).fetchall()
    for superseder_id, target_id in rows:
        conn.execute(
            "UPDATE claims SET superseded_by=? WHERE claim_id=?",
            (superseder_id, target_id),
        )

    conn.commit()
    return stats
