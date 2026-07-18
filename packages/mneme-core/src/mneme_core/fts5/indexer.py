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

import hashlib
import json
import re
import sqlite3
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

from ..privacy import redact
from ..retrieval.rrf import build_fts5_query
from ..scope import DocumentScopeError, classify_markdown_scope
from ..vault.frontmatter import load_yaml_block

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

SCHEMA_VERSION = "3"

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
    scope TEXT NOT NULL DEFAULT 'default',
    linked_notes TEXT,
    schema_version TEXT DEFAULT '3',
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

-- P6: locale profile persistence. Stores key/value pairs written at index
-- time so the query side can detect normalizer mismatches.
CREATE TABLE IF NOT EXISTS index_meta(key TEXT PRIMARY KEY, value TEXT NOT NULL);
"""

# P6: map each normalizer callable to its canonical profile string.
# The profile is stored in index_meta at index time so the TS query side
# can detect when the query-time normalizer diverges from the index-time one.
_NORMALIZER_PROFILE: dict[str, str] = {
    "normalize_tr": "tr-cldr",
    "normalize_tr_ascii_fold": "tr-ascii-fold",
    "_identity": "identity",
}


def _profile_for_normalizer(fn: Callable[[str], str]) -> str:
    """Return the canonical profile string for a normalizer callable.

    Falls back to 'identity' for any callable not in the known set, so
    third-party normalizers never crash the indexer — they simply store
    'identity' as a conservative default.
    """
    return _NORMALIZER_PROFILE.get(fn.__name__, "identity")


def read_index_meta(conn: sqlite3.Connection, key: str) -> str | None:
    """Read a single value from the index_meta table.

    Returns the stored string value, or ``None`` if the table does not
    exist or the key has no row. Safe to call on old databases that
    predate the index_meta table: the OperationalError is caught and
    treated as a missing key (soft-degrade, not hard error).
    """
    try:
        row = conn.execute(
            "SELECT value FROM index_meta WHERE key=?", (key,)
        ).fetchone()
    except sqlite3.OperationalError:
        return None
    return row[0] if row else None

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


def _extract_keypoints(body: str, max_points: int = 5) -> list[str]:
    """Extract up to *max_points* key-point strings from *body*.

    Algorithm (per lane spec):
    1. Split *body* on ``\\n\\n`` to obtain paragraphs.
    2. For each paragraph, skip if the first non-space character is ``#``
       (heading lines).
    3. Take ``paragraph.split('.')[0].strip()[:80]`` as the candidate.
    4. Append if non-empty.
    5. Stop once *max_points* entries have been collected.

    Returns a plain list of strings (no bullets, no markdown).
    """
    keypoints: list[str] = []
    for paragraph in body.split("\n\n"):
        if len(keypoints) >= max_points:
            break
        stripped = paragraph.lstrip()
        if not stripped:
            continue
        if stripped[0] == "#":
            continue
        candidate = paragraph.split(".")[0].strip()[:80]
        if candidate:
            keypoints.append(candidate)
    return keypoints


# Mapping of every column that the current ``documents`` DDL expects, beyond
# the original v1 set, to the SQL type used when ALTER-adding it.  Adding a
# new column to the SCHEMA DDL above requires a matching entry here so that
# existing databases are migrated automatically on the next ``ensure_schema``
# call.  Never remove or rename entries — doing so would prevent the migration
# from running on older databases that still lack the column.
_MIGRATION_COLUMNS: dict[str, str] = {
    "body_text": "TEXT",
    "content_hash": "TEXT",
    "trust": "TEXT",
    "key_points": "TEXT",
    "scope": "TEXT NOT NULL DEFAULT 'default'",
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
    # idx_documents_scope is created here (not in SCHEMA) so that on old
    # databases the ALTER TABLE ADD COLUMN scope always runs first.
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_documents_scope ON documents(scope)"
    )


def _should_index(
    path: Path,
    vault_root: Path,
    exclude_patterns: Iterable[str],
) -> bool:
    s = "/" + str(path.relative_to(vault_root)).replace("\\", "/") + "/"
    return not any(p in s for p in exclude_patterns)


def _parse_frontmatter(content: str) -> tuple[dict[str, Any], str]:
    """Parse frontmatter using mneme's canonical date-safe YAML loader.

    Loads the block with :func:`mneme_core.vault.frontmatter.load_yaml_block`
    (the same loader the canonical reader uses), so the index and the reader
    can never disagree about how YAML is parsed: flow-style sequences
    (``tags: [a, b]``), inline comments (``type: session  # note``), quoted
    scalars, and multiline values all resolve identically. This closes the
    drift the previous hand-rolled line splitter introduced.

    Unlike the strict reader, the indexer is deliberately lenient about *which*
    fields are present: it reads whatever ``type``/``tags``/``session_id``
    exist and never drops a file for missing structural keys, because the index
    favors recall. Returns ``({}, content)`` when there is no frontmatter block
    and ``({}, body)`` (block stripped) when the block is present but
    unparseable or not a mapping.
    """
    m = FRONTMATTER_RE.match(content)
    if not m:
        return {}, content
    body = content[m.end() :]
    try:
        data = load_yaml_block(m.group(1))
    except yaml.YAMLError:
        return {}, body
    if not isinstance(data, dict):
        return {}, body
    return data, body


def _frontmatter_tags(fm: dict[str, Any]) -> str:
    """Render the frontmatter ``tags`` field as a space-joined token string.

    YAML may yield a list (flow ``[a, b]`` or block ``- a``) or a bare scalar
    (``tags: a b``); both collapse to a single FTS-friendly token string so a
    flow-style sequence is never stored as the literal ``"[a, b]"``.
    """
    raw = fm.get("tags")
    if isinstance(raw, list):
        return " ".join(str(t) for t in raw)
    if raw is None:
        return ""
    return str(raw)


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
    # Sort the walk so document rowids are assigned in a filesystem-independent
    # order. ``rglob`` yields entries in directory order, which differs across
    # filesystems (e.g. ext4 vs NTFS); since FTS5 breaks equal-BM25 ties by
    # rowid, an unsorted walk makes ranking — and therefore the retrieval
    # benchmark's nDCG — depend on the host filesystem. Sorting makes indexing
    # deterministic and reproducible everywhere.
    paths = sorted(config.vault_root.rglob("*.md"))
    stats.total_seen = len(paths)

    # Resolve the vault root once so every file's realpath can be checked
    # against it below (vault-escape containment).
    vault_root_resolved = config.vault_root.resolve()

    # Prune-transaction fix: CREATE TEMP TABLE is a DDL statement that causes
    # an implicit commit in SQLite, which would split the intended single
    # transaction if it were placed after the upsert loop.  By issuing it
    # (and immediately clearing it) here — before any upsert accumulates —
    # the DDL auto-commit happens on an empty write set, so all upserts and
    # the subsequent DELETE prune remain in the same implicit transaction.
    # The table is local to the connection and dropped automatically on close.
    conn.execute(
        "CREATE TEMP TABLE IF NOT EXISTS _mneme_live_paths"
        "(path TEXT PRIMARY KEY)"
    )
    conn.execute("DELETE FROM _mneme_live_paths")

    # Collect relative paths for every eligible file seen this pass so that
    # we can prune stale rows at the end of a full (non-incremental) run.
    live_paths: set[str] = set()

    for md_path in paths:
        if not _should_index(md_path, config.vault_root, config.exclude_patterns):
            stats.skipped_excluded += 1
            continue

        # rglob follows symlinks, and ``_should_index`` only checks the lexical
        # relative path. Reject any file whose realpath escapes the vault root,
        # so a symlink planted inside the vault that points outside it (e.g.
        # ``vault/private.md`` -> ``~/.ssh/id_rsa``) is not read and indexed,
        # which would otherwise leak out-of-vault file contents through search.
        # Mirrors the TypeScript ``assertWithinVault`` write-path guard.
        try:
            resolved = md_path.resolve()
        except OSError:
            stats.skipped_error += 1
            continue
        if not resolved.is_relative_to(vault_root_resolved):
            stats.skipped_excluded += 1
            continue

        rel_path = str(md_path.relative_to(config.vault_root)).replace("\\", "/")
        if redact(rel_path) != rel_path:
            # ``path`` is the document identity and cannot be replaced with a
            # lossy redaction token without collision risk. Skip explicitly
            # private filenames instead of persisting the secret.
            stats.skipped_excluded += 1
            continue

        try:
            mtime = md_path.stat().st_mtime
            if since_mtime > 0 and mtime <= since_mtime:
                stats.skipped_unchanged += 1
                # Still track the path so a full-pass prune never removes
                # unchanged-but-still-present files.
                live_paths.add(rel_path)
                continue
            raw_bytes = md_path.read_bytes()
            raw_content = raw_bytes.decode("utf-8", errors="replace")
            scope_info = classify_markdown_scope(raw_content)
            if redact(scope_info.scope) != scope_info.scope:
                stats.skipped_error += 1
                continue
            content = redact(raw_content)
            content_hash_val = hashlib.sha256(content.encode("utf-8")).hexdigest()
        except (OSError, DocumentScopeError):
            stats.skipped_error += 1
            continue

        trust_val = "user"
        live_paths.add(rel_path)

        # fm and body are both derived from the already-redacted `content`
        # string (redact() ran above), so all downstream stores inherit redaction.
        fm, body = _parse_frontmatter(content)
        title = _extract_title(content, md_path)
        title_normalized = config.normalize(title)
        content_normalized = config.normalize_for_fts(body)
        tags = _frontmatter_tags(fm)
        tags_normalized = config.normalize(tags)
        fm_type = str(fm.get("type") or "")
        fm_session_id = str(fm.get("session_id") or "")
        fm_scope = scope_info.scope
        wikilinks = _extract_wikilinks(body)
        wikilinks_normalized = config.normalize(wikilinks)
        now_iso = datetime.now(UTC).isoformat()
        keypoints = _extract_keypoints(body)

        # Normalizers and extractors are injectable extension points. Treat
        # their output as untrusted and reapply the canonical redactor to every
        # user-derived value immediately before the SQLite sinks.
        title = redact(title)
        title_normalized = redact(title_normalized)
        content = redact(content)
        body = redact(body)
        content_normalized = redact(content_normalized)
        tags = redact(tags)
        tags_normalized = redact(tags_normalized)
        fm_type = redact(fm_type)
        fm_session_id = redact(fm_session_id)
        wikilinks = redact(wikilinks)
        wikilinks_normalized = redact(wikilinks_normalized)
        keypoints = [redact(point) for point in keypoints]
        content_hash_val = hashlib.sha256(content.encode("utf-8")).hexdigest()

        conn.execute(
            """INSERT INTO documents
               (title, title_normalized, path, content_raw, body_text,
                content_size, mtime, tags, frontmatter_type, session_id,
                linked_notes, indexed_at, content_hash, trust, key_points,
                scope)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                 indexed_at=excluded.indexed_at,
                 content_hash=excluded.content_hash,
                 trust=excluded.trust,
                 key_points=excluded.key_points,
                 scope=excluded.scope""",
            (
                title,
                title_normalized,
                rel_path,
                content,
                body,
                len(content),
                mtime,
                tags,
                fm_type,
                fm_session_id,
                wikilinks,
                now_iso,
                content_hash_val,
                trust_val,
                json.dumps(keypoints),
                fm_scope,
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
                    redact(config.normalize_ascii(title)),
                    redact(config.normalize_ascii_for_fts(body)),
                    redact(config.normalize_ascii(tags)),
                    redact(config.normalize_ascii(wikilinks)),
                ),
            )

        stats.indexed += 1

    # P0-2: prune rows whose files no longer exist on disk.
    # Only performed on a full pass (since_mtime == 0.0) to keep incremental
    # runs cheap and to avoid deleting files that were simply older than the
    # since_mtime threshold.
    # When live_paths is empty every eligible file was excluded — delete ALL
    # rows so stale entries from a previously indexed vault do not survive.
    if since_mtime == 0.0:
        if live_paths:
            # prune-overflow fix: the TEMP table was already created and
            # cleared above (before the file-walk) so the DDL auto-commit
            # happens before any upsert accumulates.  Populate it now with
            # the live paths collected during this pass, then DELETE stale rows.
            conn.executemany(
                "INSERT OR IGNORE INTO _mneme_live_paths(path) VALUES (?)",
                ((p,) for p in live_paths),
            )
            conn.execute(
                "DELETE FROM documents_fts WHERE rowid IN ("
                "  SELECT id FROM documents"
                "  WHERE path NOT IN (SELECT path FROM _mneme_live_paths))"
            )
            conn.execute(
                "DELETE FROM documents_ascii_fts WHERE rowid IN ("
                "  SELECT id FROM documents"
                "  WHERE path NOT IN (SELECT path FROM _mneme_live_paths))"
            )
            conn.execute(
                "DELETE FROM documents"
                " WHERE path NOT IN (SELECT path FROM _mneme_live_paths)"
            )
        else:
            # No eligible files survived the pass — wipe all rows.
            conn.execute("DELETE FROM documents_fts")
            conn.execute("DELETE FROM documents_ascii_fts")
            conn.execute("DELETE FROM documents")

    # P6: persist the normalizer profile so the query side can detect
    # locale mismatches without re-reading every file.
    profile = _profile_for_normalizer(config.normalize)
    conn.execute(
        "INSERT INTO index_meta(key, value) VALUES(?, ?)"
        " ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        ("normalization_profile", profile),
    )
    ascii_profile = (
        _profile_for_normalizer(config.normalize_ascii)
        if config.normalize_ascii is not None
        else "disabled"
    )
    conn.execute(
        "INSERT INTO index_meta(key, value) VALUES(?, ?)"
        " ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        ("ascii_normalization_profile", ascii_profile),
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
        # Use the same production query builder so benchmark latencies reflect
        # the OR-of-phrases form actually executed at retrieval time, not a
        # single-phrase query that diverges from production behavior.
        q_fts = build_fts5_query(q_norm)
        if not q_fts:
            latencies_ms.append(-1.0)
            result_counts.append(0)
            continue
        try:
            t0 = time.perf_counter()
            rows = conn.execute(
                "SELECT rowid FROM documents_fts WHERE documents_fts MATCH ? LIMIT 50",
                (q_fts,),
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
