"""FTS5 BM25 indexer for the mneme vault.

Walks every ``*.md`` file under the configured vault root, parses
frontmatter, applies optional locale-aware normalization, and inserts
metadata into a ``documents`` table plus normalized content into the
``documents_fts`` virtual table. The virtual table uses SQLite's
``unicode61`` tokenizer with ``remove_diacritics 2`` so diacritic-only
differences fold at the tokenizer layer.

The indexer is idempotent: re-running upserts by path uniqueness, so a
second run on an unchanged vault leaves the row count constant and
overwrites no content. An optional ``since_mtime`` argument enables
cheap incremental rebuilds.

The indexer is intentionally side-effect free at import time. Callers
own the SQLite connection and the ``IndexerConfig`` that supplies
paths and normalizers.
"""

from __future__ import annotations

import re
import sqlite3
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

DEFAULT_EXCLUDE_PATTERNS: tuple[str, ...] = (
    "/.git/",
    "/node_modules/",
    "/.claude/",
    "/.obsidian/",
    "/.trash/",
    "/.idea/",
    "/__pycache__/",
    "/.mneme/",
)

SCHEMA_VERSION = "2"

SCHEMA = """
CREATE TABLE IF NOT EXISTS documents(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT,
    title_normalized TEXT,
    path TEXT UNIQUE NOT NULL,
    content_raw TEXT,
    body_text TEXT,
    content_size INTEGER,
    mtime REAL,
    tags TEXT,
    frontmatter_type TEXT,
    session_id TEXT,
    linked_notes TEXT,
    schema_version TEXT DEFAULT '2',
    language TEXT DEFAULT 'en',
    indexed_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_documents_mtime ON documents(mtime);
CREATE INDEX IF NOT EXISTS idx_documents_path ON documents(path);
CREATE INDEX IF NOT EXISTS idx_documents_frontmatter_type ON documents(frontmatter_type);

CREATE VIRTUAL TABLE IF NOT EXISTS documents_fts USING fts5(
    title,
    content,
    tags,
    linked_notes,
    tokenize='unicode61 remove_diacritics 2'
);

-- Dual-key ASCII-fold index for Turkish single-token recall. Holds the same
-- documents keyed by an ASCII-i fold (İ/I/ı/i all collapse to plain i) so an
-- ASCII-capital query like "Izmir" recalls a vault that stored the proper
-- dotted "İzmir". FTS5 virtual tables cannot be ALTER-extended, so this is a
-- sibling table created idempotently rather than a column on documents_fts;
-- it stays empty (zero overhead) unless an ASCII normalizer is configured.
CREATE VIRTUAL TABLE IF NOT EXISTS documents_ascii_fts USING fts5(
    title,
    content,
    tags,
    linked_notes,
    tokenize='unicode61 remove_diacritics 2'
);
"""

FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)
WIKILINK_RE = re.compile(r"\[\[([^\]]+)\]\]")


def _identity(s: str) -> str:
    return s


@dataclass
class IndexStats:
    """Aggregate counters returned by a single index pass."""

    indexed: int = 0
    skipped_excluded: int = 0
    skipped_unchanged: int = 0
    skipped_error: int = 0
    total_seen: int = 0


@dataclass
class IndexerConfig:
    """Configuration for the FTS5 indexer.

    Attributes:
        vault_root: directory whose ``*.md`` files will be scanned.
        db_path: SQLite database file path. Parent directory is created
            automatically on ``connect``.
        normalize: token-level normalizer applied to title, tags, and
            wikilinks. Defaults to identity; pass
            ``mneme_core.fts5.locale.tr.normalize_tr`` for Turkish
            vaults.
        normalize_for_fts: whitespace-collapsing variant for body
            content. Defaults to identity.
        normalize_ascii: optional ASCII-fold normalizer for the dual-key
            ``documents_ascii_fts`` table (title, tags, wikilinks). Pass
            ``mneme_core.fts5.locale.tr.normalize_tr_ascii_fold`` for
            Turkish vaults to recover ASCII-capital recall. When ``None``
            (the default) the ASCII table is left empty and the indexer
            behaves exactly as before.
        normalize_ascii_for_fts: whitespace-collapsing ASCII-fold variant
            for body content. Must be supplied together with
            ``normalize_ascii`` to enable dual-key indexing.
        exclude_patterns: substrings that, if present anywhere in a
            path relative to the vault root (with a leading and
            trailing slash added), cause the file to be skipped.
    """

    vault_root: Path
    db_path: Path
    normalize: Callable[[str], str] = _identity
    normalize_for_fts: Callable[[str], str] = _identity
    normalize_ascii: Callable[[str], str] | None = None
    normalize_ascii_for_fts: Callable[[str], str] | None = None
    exclude_patterns: tuple[str, ...] = DEFAULT_EXCLUDE_PATTERNS


@dataclass
class BenchmarkResult:
    """Latency distribution returned by ``benchmark_queries``."""

    queries: int
    failed: int
    p50_ms: float
    p95_ms: float
    p99_ms: float
    avg_results: float
    pass_criterion_met: bool


def connect(db_path: Path) -> sqlite3.Connection:
    """Open the FTS5 database with sensible pragmas applied.

    The caller is responsible for closing the connection. Creates the
    parent directory if it does not exist.
    """
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA synchronous = NORMAL")
    conn.execute("PRAGMA cache_size = -64000")
    return conn


def ensure_schema(conn: sqlite3.Connection) -> None:
    """Create the ``documents`` and ``documents_fts`` tables if needed.

    Also runs ``migrate_schema`` so that pre-existing databases receive
    any new columns added after their initial creation.
    """
    conn.executescript(SCHEMA)
    migrate_schema(conn)


# Mapping of every column that the current ``documents`` DDL expects, beyond
# the original v1 set, to the SQL type used when ALTER-adding it.  Adding a
# new column to the SCHEMA DDL above requires a matching entry here so that
# existing databases are migrated automatically on the next ``ensure_schema``
# call.  Never remove or rename entries — doing so would prevent the migration
# from running on older databases that still lack the column.
_MIGRATION_COLUMNS: dict[str, str] = {
    "body_text": "TEXT",
}


def migrate_schema(conn: sqlite3.Connection) -> None:
    """Apply lightweight additive migrations to an existing ``documents`` table.

    Derives the expected column set from :data:`_MIGRATION_COLUMNS` and
    issues ``ALTER TABLE … ADD COLUMN`` for any column that is absent.
    The operation is idempotent: calling it twice on the same database
    produces no change on the second call.

    Design constraints:

    * Only ``ALTER TABLE ADD COLUMN`` is used — SQLite does not support
      dropping or renaming columns in this mode, so the migration is
      always purely additive.
    * ``_MIGRATION_COLUMNS`` is the canonical source of truth for which
      columns need to be present.  ``SCHEMA_VERSION`` signals the overall
      intent to callers (e.g. ``mneme doctor index-schema``).
    * Safe to call on a brand-new database: every column in
      ``_MIGRATION_COLUMNS`` already exists in the DDL, so all ALTERs
      are skipped.
    """
    existing_cols = {
        row[1]
        for row in conn.execute("PRAGMA table_info(documents)")
    }
    for col_name, col_type in _MIGRATION_COLUMNS.items():
        if col_name not in existing_cols:
            conn.execute(
                f"ALTER TABLE documents ADD COLUMN {col_name} {col_type}"
            )


def _should_index(
    path: Path,
    vault_root: Path,
    exclude_patterns: Iterable[str],
) -> bool:
    s = "/" + str(path.relative_to(vault_root)).replace("\\", "/") + "/"
    return not any(p in s for p in exclude_patterns)


def _dequote(value: str) -> str:
    """Strip outer YAML single or double quotes from a scalar value.

    Phase J Day 3 surfaced a drift: the migration tool emits YAML-correct
    single-quoted scalars like ``type: 'observation'`` but the prior
    parser stored the literal including the quotes. Type-based filters
    then matched nothing because the stored value was ``'observation'``
    rather than ``observation``. This helper handles the single- and
    double-quoted cases plus YAML's single-quote double-escape for
    apostrophes inside single-quoted scalars (``'it''s fine'`` ->
    ``it's fine``). Other escape sequences are out of scope; this is a
    lightweight parser, not a full YAML implementation.
    """
    s = value.strip()
    if len(s) >= 2 and s[0] == s[-1] and s[0] in ("'", '"'):
        inner = s[1:-1]
        if s[0] == "'":
            inner = inner.replace("''", "'")
        return inner
    return s


def _parse_frontmatter(content: str) -> tuple[dict[str, str], str]:
    m = FRONTMATTER_RE.match(content)
    if not m:
        return {}, content
    fm: dict[str, str] = {}
    list_key: str | None = None
    list_items: list[str] = []

    def _flush() -> None:
        nonlocal list_key, list_items
        if list_key is not None:
            fm[list_key] = " ".join(list_items)
            list_key = None
            list_items = []

    for line in m.group(1).splitlines():
        stripped = line.strip()
        # Continuation of a YAML block sequence under the pending key
        # ("  - item"). Without this, block-style values such as
        # "tags:\n  - a\n  - b" (what the migration tool and yaml.safe_dump
        # both emit) collapse to an empty scalar and never reach the index.
        if list_key is not None and stripped.startswith("- "):
            list_items.append(_dequote(stripped[2:]))
            continue
        _flush()
        if ":" in line:
            k, v = line.split(":", 1)
            key = k.strip()
            if v.strip() == "":
                # Empty value may head a block sequence; defer until the
                # following lines are seen. Defaults to "" if none follow.
                list_key = key
                fm[key] = ""
            else:
                fm[key] = _dequote(v)
    _flush()
    return fm, content[m.end() :]


def _extract_title(content: str, path: Path) -> str:
    body = content
    fm_match = FRONTMATTER_RE.match(content)
    if fm_match:
        body = content[fm_match.end() :]
    for line in body.splitlines()[:20]:
        stripped = line.strip()
        if stripped.startswith("# ") and len(stripped) > 2:
            return stripped[2:].strip()
    return path.stem


def _extract_wikilinks(content: str) -> str:
    return " ".join(WIKILINK_RE.findall(content))


def index_vault(
    conn: sqlite3.Connection,
    config: IndexerConfig,
    since_mtime: float = 0.0,
) -> IndexStats:
    """Walk the vault and index every eligible markdown file.

    Args:
        conn: an open SQLite connection where ``ensure_schema`` has
            already been called.
        config: paths and normalizers.
        since_mtime: if greater than zero, files whose ``stat().st_mtime``
            is less than or equal to this value are skipped as
            unchanged.

    Returns:
        ``IndexStats`` with counters for indexed, excluded, unchanged,
        errored, and total-seen files.
    """
    stats = IndexStats()
    paths = list(config.vault_root.rglob("*.md"))
    stats.total_seen = len(paths)

    # Collect relative paths for every eligible file seen this pass so that
    # we can prune stale rows at the end of a full (non-incremental) run.
    live_paths: set[str] = set()

    for md_path in paths:
        if not _should_index(md_path, config.vault_root, config.exclude_patterns):
            stats.skipped_excluded += 1
            continue

        rel_path = str(md_path.relative_to(config.vault_root)).replace("\\", "/")

        try:
            mtime = md_path.stat().st_mtime
            if since_mtime > 0 and mtime <= since_mtime:
                stats.skipped_unchanged += 1
                # Still track the path so a full-pass prune never removes
                # unchanged-but-still-present files.
                live_paths.add(rel_path)
                continue
            content = md_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            stats.skipped_error += 1
            continue

        live_paths.add(rel_path)

        fm, body = _parse_frontmatter(content)
        title = _extract_title(content, md_path)
        title_normalized = config.normalize(title)
        content_normalized = config.normalize_for_fts(body)
        tags = fm.get("tags", "")
        tags_normalized = config.normalize(tags)
        wikilinks = _extract_wikilinks(body)
        wikilinks_normalized = config.normalize(wikilinks)
        now_iso = datetime.now(UTC).isoformat()

        conn.execute(
            """INSERT INTO documents
               (title, title_normalized, path, content_raw, body_text,
                content_size, mtime, tags, frontmatter_type, session_id,
                linked_notes, indexed_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(path) DO UPDATE SET
                 title=excluded.title,
                 title_normalized=excluded.title_normalized,
                 content_raw=excluded.content_raw,
                 body_text=excluded.body_text,
                 content_size=excluded.content_size,
                 mtime=excluded.mtime,
                 tags=excluded.tags,
                 frontmatter_type=excluded.frontmatter_type,
                 session_id=excluded.session_id,
                 linked_notes=excluded.linked_notes,
                 indexed_at=excluded.indexed_at""",
            (
                title,
                title_normalized,
                rel_path,
                content,
                body,
                len(content),
                mtime,
                tags,
                fm.get("type", ""),
                fm.get("session_id", ""),
                wikilinks,
                now_iso,
            ),
        )

        row = conn.execute(
            "SELECT id FROM documents WHERE path=?", (rel_path,)
        ).fetchone()
        doc_id = row[0]
        conn.execute("DELETE FROM documents_fts WHERE rowid=?", (doc_id,))
        conn.execute(
            "INSERT INTO documents_fts (rowid, title, content, tags, linked_notes) "
            "VALUES (?, ?, ?, ?, ?)",
            (
                doc_id,
                title_normalized,
                content_normalized,
                tags_normalized,
                wikilinks_normalized,
            ),
        )

        # Dual-key ASCII-fold row. Always clear the prior row (idempotent,
        # harmless when the table is empty); repopulate only when an ASCII
        # normalizer pair is configured so non-Turkish vaults pay nothing.
        conn.execute("DELETE FROM documents_ascii_fts WHERE rowid=?", (doc_id,))
        if (
            config.normalize_ascii is not None
            and config.normalize_ascii_for_fts is not None
        ):
            conn.execute(
                "INSERT INTO documents_ascii_fts "
                "(rowid, title, content, tags, linked_notes) "
                "VALUES (?, ?, ?, ?, ?)",
                (
                    doc_id,
                    config.normalize_ascii(title),
                    config.normalize_ascii_for_fts(body),
                    config.normalize_ascii(tags),
                    config.normalize_ascii(wikilinks),
                ),
            )

        stats.indexed += 1

    # P0-2: prune rows whose files no longer exist on disk.
    # Only performed on a full pass (since_mtime == 0.0) to keep incremental
    # runs cheap and to avoid deleting files that were simply older than the
    # since_mtime threshold.
    if since_mtime == 0.0 and live_paths:
        placeholders = ",".join("?" * len(live_paths))
        live_list = list(live_paths)
        conn.execute(
            f"DELETE FROM documents_fts WHERE rowid IN ("
            f"  SELECT id FROM documents WHERE path NOT IN ({placeholders}))",
            live_list,
        )
        conn.execute(
            f"DELETE FROM documents_ascii_fts WHERE rowid IN ("
            f"  SELECT id FROM documents WHERE path NOT IN ({placeholders}))",
            live_list,
        )
        conn.execute(
            f"DELETE FROM documents WHERE path NOT IN ({placeholders})",
            live_list,
        )

    conn.commit()
    return stats


def benchmark_queries(
    conn: sqlite3.Connection,
    queries: list[str],
    normalize: Callable[[str], str] = _identity,
    pass_threshold_ms: float = 100.0,
) -> BenchmarkResult:
    """Run a list of queries against the FTS5 index and return latency stats.

    Args:
        conn: SQLite connection.
        queries: list of raw query strings. Each is normalized with
            ``normalize`` and then quoted to be safe for FTS5 MATCH.
        normalize: same normalizer used at ingest, applied here at
            query time for symmetry.
        pass_threshold_ms: p95 below this value sets
            ``pass_criterion_met`` to True.

    Returns:
        ``BenchmarkResult`` with p50/p95/p99 in milliseconds.

    Raises:
        RuntimeError: if no query returned a valid latency (for example
            empty input list, or every query raised
            ``sqlite3.OperationalError``).
    """
    latencies_ms: list[float] = []
    result_counts: list[int] = []
    for q in queries:
        q_norm = normalize(q)
        q_escaped = '"' + q_norm.replace('"', '""') + '"'
        try:
            t0 = time.perf_counter()
            rows = conn.execute(
                "SELECT rowid FROM documents_fts WHERE documents_fts MATCH ? LIMIT 50",
                (q_escaped,),
            ).fetchall()
            elapsed = (time.perf_counter() - t0) * 1000
            latencies_ms.append(elapsed)
            result_counts.append(len(rows))
        except sqlite3.OperationalError:
            latencies_ms.append(-1.0)
            result_counts.append(0)

    valid = sorted(x for x in latencies_ms if x >= 0)
    if not valid:
        raise RuntimeError("No valid query latencies were collected.")

    p50 = valid[len(valid) // 2]
    p95 = valid[min(int(len(valid) * 0.95), len(valid) - 1)]
    p99 = valid[min(int(len(valid) * 0.99), len(valid) - 1)]
    avg = sum(result_counts) / len(result_counts) if result_counts else 0.0

    return BenchmarkResult(
        queries=len(queries),
        failed=sum(1 for x in latencies_ms if x < 0),
        p50_ms=round(p50, 2),
        p95_ms=round(p95, 2),
        p99_ms=round(p99, 2),
        avg_results=round(avg, 2),
        pass_criterion_met=p95 < pass_threshold_ms,
    )
