"""Adapter Protocol for head-to-head retrieval benchmarks.

Each adapter wraps one memory system (mneme, claude-mem, mem0, ...) in
a uniform interface so the benchmark harness can issue identical
queries to multiple systems and aggregate comparable metrics.

The v1.0 benchmark ships two adapters:

* :class:`MnemeAdapter` - production: migrates a claude-mem fixture
  via the TS CLI, indexes the resulting markdown with the production
  FTS5 indexer, and queries through the production ``retrieve``
  pipeline.

* :class:`ClaudeMemAdapter` - stub: documents how to wire a real
  claude-mem installation. The adapter reports
  ``available=False`` when no claude-mem binary is on PATH so the
  benchmark gracefully skips the comparison until the operator
  invokes it post-dogfood-week.

Adding a third adapter (mem0, supermemory, episodic-memory) is a
matter of implementing :class:`MemoryAdapter` and registering it in
``run.py``. No harness changes needed.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_REPO_ROOT / "packages" / "mneme-core" / "src"))

from mneme_core.fts5.indexer import (  # noqa: E402
    IndexerConfig,
    connect,
    ensure_schema,
    index_vault,
)
from mneme_core.retrieval.rrf import RetrievalConfig, retrieve  # noqa: E402


@dataclass
class AdapterStatus:
    """Per-adapter runtime probe.

    ``available`` is False when the adapter cannot run at all on this
    host (missing binary, missing credentials, etc.). The harness
    treats unavailability as a structured skip rather than an error.
    """

    name: str
    available: bool
    reason: str = ""
    extras: dict[str, object] = field(default_factory=dict)


@dataclass
class AdapterHit:
    """Comparable hit shape across adapters."""

    doc_id: str
    score: float


class MemoryAdapter(Protocol):
    """The contract the harness expects from every memory system."""

    def status(self) -> AdapterStatus: ...

    def migrate(self, source_db: Path, workdir: Path) -> dict[str, object]: ...

    def search(self, query: str, top_k: int = 10) -> list[AdapterHit]: ...


class MnemeAdapter:
    """Production mneme adapter.

    Migration: invoke the TS migrator via ``npx tsx``.
    Indexing: ``mneme_core.fts5.indexer``.
    Retrieval: ``mneme_core.retrieval.rrf.retrieve``.
    """

    def __init__(self) -> None:
        self._vault: Path | None = None
        self._db: Path | None = None

    def status(self) -> AdapterStatus:
        npx = shutil.which("npx")
        if npx is None:
            return AdapterStatus(
                name="mneme",
                available=False,
                reason="npx not on PATH; install Node and run pnpm install.",
            )
        return AdapterStatus(name="mneme", available=True, extras={"npx": npx})

    def migrate(self, source_db: Path, workdir: Path) -> dict[str, object]:
        vault = workdir / "mneme-vault"
        (vault / ".mneme").mkdir(parents=True, exist_ok=True)
        cli_entry = _REPO_ROOT / "packages" / "mneme-mcp" / "src" / "cli" / "index.ts"
        npx = shutil.which("npx")
        if npx is None:
            return {"status": "error", "reason": "npx not found"}
        cmd = [
            npx,
            "--yes",
            "tsx",
            str(cli_entry),
            "migrate-from-claude-mem",
            "--source",
            str(source_db),
            "--vault",
            str(vault),
        ]
        proc = subprocess.run(  # noqa: S603
            cmd,
            cwd=_REPO_ROOT,
            capture_output=True,
            text=True,
            check=False,
            timeout=300,
        )
        if proc.returncode != 0:
            return {"status": "error", "rc": proc.returncode, "stderr": proc.stderr}

        # Build FTS5 index over the migrated vault so retrieve() can read.
        db_path = vault / ".mneme" / "fts5.sqlite"
        conn = connect(db_path)
        try:
            ensure_schema(conn)
            index_vault(
                conn,
                IndexerConfig(vault_root=vault, db_path=db_path),
            )
        finally:
            conn.close()

        self._vault = vault
        self._db = db_path
        return {"status": "ok", "vault": str(vault), "db": str(db_path)}

    def search(self, query: str, top_k: int = 10) -> list[AdapterHit]:
        if self._db is None:
            return []
        cfg = RetrievalConfig(fts5_db=self._db, top_n_final=top_k)
        hits = retrieve(query, cfg)
        out: list[AdapterHit] = []
        for h in hits:
            doc_id = Path(h.path).stem
            out.append(AdapterHit(doc_id=doc_id, score=h.rrf_score or h.score))
        return out


class ClaudeMemAdapter:
    """Adapter for an actual claude-mem v13.2.0 installation.

    Reports ``available=False`` until the binary is wired. To exercise
    the comparison side:

    1. Install claude-mem v13.2.0 in the host environment.
    2. Either place it on PATH or set ``CLAUDE_MEM_BIN`` to the
       absolute binary path.
    3. Set ``CLAUDE_MEM_DB`` to the path the benchmark's fixture SQLite
       will be copied to, or ensure your installation honors the
       ``--db`` flag passed by this adapter.
    4. Re-run ``make bench-head-to-head``.

    The adapter is defensive: any subprocess failure, parse error, or
    timeout results in a zero-hit response for that query rather than
    a crash. The harness then naturally penalizes the unavailable side
    in the metric aggregation without fabricating data.

    Output parsing supports three shapes commonly emitted by JSON CLIs:

    * ``[{"id": ..., "score": ...}, ...]`` (top-level array)
    * ``{"hits": [...]}`` (envelope under ``hits``)
    * ``{"results": [...]}`` (envelope under ``results``)
    """

    _QUERY_TIMEOUT_S: int = 30
    _DEFAULT_SUBCOMMAND: str = "search"

    def __init__(
        self,
        *,
        timeout_s: int | None = None,
        extra_search_args: list[str] | None = None,
    ) -> None:
        self._bin: str | None = None
        self._source_db: Path | None = None
        self._db_for_subprocess: Path | None = None
        self._timeout_s = timeout_s if timeout_s is not None else self._QUERY_TIMEOUT_S
        self._extra_search_args = list(extra_search_args or [])

    def _resolve_bin(self) -> str | None:
        import os
        explicit = os.environ.get("CLAUDE_MEM_BIN", "")
        if explicit:
            return explicit
        return shutil.which("claude-mem")

    def status(self) -> AdapterStatus:
        bin_path = self._resolve_bin()
        if bin_path is None:
            return AdapterStatus(
                name="claude-mem",
                available=False,
                reason=(
                    "claude-mem binary not found. To wire the actual "
                    "head-to-head: install claude-mem v13.2.0 and set "
                    "CLAUDE_MEM_BIN, or place the binary on PATH."
                ),
            )
        self._bin = bin_path
        return AdapterStatus(
            name="claude-mem",
            available=True,
            extras={"bin": bin_path, "timeout_s": self._timeout_s},
        )

    def migrate(self, source_db: Path, workdir: Path) -> dict[str, object]:
        # claude-mem owns its own DB path. The benchmark's source_db is
        # a synthetic fixture; we copy it to a known location and pass
        # that path via ``--db`` on every search invocation. Operators
        # whose claude-mem build does not accept ``--db`` can override
        # by setting CLAUDE_MEM_DB upfront.
        import os
        target = workdir / "claude-mem-fixture.db"
        try:
            shutil.copyfile(source_db, target)
        except OSError as exc:
            return {"status": "error", "reason": f"copy failed: {exc}"}

        env_db = os.environ.get("CLAUDE_MEM_DB", "")
        self._db_for_subprocess = Path(env_db) if env_db else target
        self._source_db = source_db
        return {
            "status": "ok",
            "source_db": str(source_db),
            "search_db": str(self._db_for_subprocess),
        }

    def _build_command(self, query: str, top_k: int) -> list[str]:
        if self._bin is None:
            raise RuntimeError("status() must be called before search()")
        cmd: list[str] = [self._bin, self._DEFAULT_SUBCOMMAND]
        if self._db_for_subprocess is not None:
            cmd.extend(["--db", str(self._db_for_subprocess)])
        cmd.extend(["--json", "--top-k", str(top_k)])
        cmd.extend(self._extra_search_args)
        cmd.append("--")
        cmd.append(query)
        return cmd

    @staticmethod
    def _parse_output(stdout: str) -> list[AdapterHit]:
        import json
        if not stdout.strip():
            return []
        try:
            parsed = json.loads(stdout)
        except json.JSONDecodeError:
            return []
        if isinstance(parsed, dict):
            rows = parsed.get("hits") or parsed.get("results") or []
        elif isinstance(parsed, list):
            rows = parsed
        else:
            return []
        hits: list[AdapterHit] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            doc_id = row.get("id") or row.get("doc_id") or row.get("path")
            if doc_id is None:
                continue
            score_raw = row.get("score", row.get("rrf_score", 0.0))
            try:
                score = float(score_raw)
            except (TypeError, ValueError):
                score = 0.0
            hits.append(AdapterHit(doc_id=str(doc_id), score=score))
        return hits

    def search(self, query: str, top_k: int = 10) -> list[AdapterHit]:
        if self._bin is None:
            self.status()
        if self._bin is None:
            return []
        cmd = self._build_command(query, top_k)
        try:
            proc = subprocess.run(  # noqa: S603
                cmd,
                capture_output=True,
                text=True,
                check=False,
                timeout=self._timeout_s,
            )
        except (subprocess.TimeoutExpired, OSError):
            return []
        if proc.returncode != 0:
            return []
        return self._parse_output(proc.stdout)
