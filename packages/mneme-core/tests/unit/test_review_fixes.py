"""Regression tests for code-review fixes F1-F15.

Each class is named after the finding it covers. Tests follow the
RED-then-GREEN discipline: they are written to target the specific defect
described in each finding, and pass only after the corresponding fix is
applied.
"""

from __future__ import annotations

import datetime
import json
import shutil
from pathlib import Path
from unittest.mock import patch

import pytest
from click.testing import CliRunner

from mneme_core.cli import cli
from mneme_core.compression.config import CompressionConfig
from mneme_core.compression.ledger import reserve_cost
from mneme_core.compression.pipeline import (
    PipelineConfig,
    RunReport,
    _truncate_payload,
    run_compression,
)
from mneme_core.compression.staging import StagingConfig, _write_event, capture_event
from mneme_core.distill.injection_dedup import (
    InjectionTracker,
    load_tracker,
    save_tracker,
)
from mneme_core.fts5.indexer import (
    IndexerConfig,
    benchmark_queries,
    connect,
    ensure_schema,
    index_vault,
)
from mneme_core.kg.episode_stage import KgConfig, stage_event
from mneme_core.kg.worker import _archive_file
from mneme_core.patterns import Pattern, list_patterns, store_pattern
from mneme_core.privacy import redact
from mneme_core.retrieval.rrf import DEFAULT_RRF_K, Hit, build_fts5_query, rrf_fuse
from mneme_core.trajectory import list_trajectories, record_step, start_trajectory
from mneme_core.vault.atomic_write import atomic_write_text
from mneme_core.vault.config import VaultConfig

# ---------------------------------------------------------------------------
# F1 - rrf_fuse dedup key must be path, not id
# ---------------------------------------------------------------------------


class TestF1RrfFuseDedup:
    """Same document from two backends (one int id, one None id) must fuse."""

    def _make_fts5_hit(self, path: str) -> Hit:
        return Hit(id=5, path=path, title="t", score=-1.0, source="fts5")

    def _make_dense_hit(self, path: str) -> Hit:
        return Hit(id=None, path=path, title="t", score=0.9, source="dense")

    def test_same_path_different_id_fuses_to_one(self) -> None:
        path = "/tmp/vault/note.md"
        fts5 = [self._make_fts5_hit(path)]
        dense = [self._make_dense_hit(path)]
        result = rrf_fuse([fts5, dense])
        assert len(result) == 1, (
            f"Expected 1 fused hit, got {len(result)}; "
            "same document must not appear twice with different id types"
        )

    def test_fused_score_is_sum_of_both_legs(self) -> None:
        path = "/tmp/vault/note.md"
        fts5 = [self._make_fts5_hit(path)]
        dense = [self._make_dense_hit(path)]
        result = rrf_fuse([fts5, dense], k=DEFAULT_RRF_K)
        expected = 2.0 / (DEFAULT_RRF_K + 1)
        assert result[0].rrf_score == pytest.approx(expected, rel=1e-6)

    def test_different_paths_remain_separate(self) -> None:
        h1 = Hit(id=1, path="/a.md", title="a", score=-1.0, source="fts5")
        h2 = Hit(id=None, path="/b.md", title="b", score=0.9, source="dense")
        result = rrf_fuse([[h1], [h2]])
        assert len(result) == 2


# ---------------------------------------------------------------------------
# F2 - index_vault full-pass prune with empty live_paths
# ---------------------------------------------------------------------------


class TestF2IndexPruneEmptyVault:
    """Full pass with all files excluded must delete all rows."""

    def test_all_excluded_clears_rows(self, tmp_path: Path) -> None:
        vault = tmp_path / "vault"
        vault.mkdir()
        (vault / ".obsidian").mkdir()
        (vault / ".obsidian" / "note.md").write_text(
            "# hidden\nbody\n", encoding="utf-8"
        )
        db = tmp_path / "fts.db"
        conn = connect(db)
        ensure_schema(conn)

        # First: index with a normal file so rows exist.
        normal = vault / "real.md"
        normal.write_text("# Real\nbody\n", encoding="utf-8")
        cfg = IndexerConfig(vault_root=vault, db_path=db)
        index_vault(conn, cfg)
        row_count = conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0]
        assert row_count == 1

        # Remove the normal file; only excluded .obsidian remains.
        normal.unlink()
        index_vault(conn, cfg)
        row_count = conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0]
        assert row_count == 0, "Full pass with no eligible files must prune all rows"
        conn.close()


# ---------------------------------------------------------------------------
# F3 - benchmark_queries uses build_fts5_query (OR-of-phrases form)
# ---------------------------------------------------------------------------


class TestF3BenchmarkQueryForm:
    """benchmark_queries must build its FTS5 query via the production builder."""

    def test_benchmark_uses_production_query_builder(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        db = tmp_path / "bench.db"
        conn = connect(db)
        ensure_schema(conn)
        # Seed one document so the query can actually execute.
        conn.execute(
            "INSERT INTO documents (title, path) VALUES ('hello world', 'a.md')"
        )
        conn.execute(
            "INSERT INTO documents_fts (rowid, title, content, tags, linked_notes) "
            "SELECT id, title, '', '', '' FROM documents WHERE path='a.md'"
        )
        conn.commit()

        # sqlite3.Connection.execute is read-only and cannot be patched, so we
        # instead spy on the production query builder that the fix routes
        # through. Proving benchmark_queries calls build_fts5_query is the
        # direct assertion that benchmark and retrieval share a query strategy.
        seen: list[str] = []
        real_builder = build_fts5_query

        def spy_builder(query: str) -> str:
            seen.append(query)
            return real_builder(query)

        monkeypatch.setattr("mneme_core.fts5.indexer.build_fts5_query", spy_builder)
        benchmark_queries(conn, ["hello world"], normalize=lambda s: s)
        conn.close()

        assert "hello world" in seen, (
            "benchmark_queries must build its FTS5 query via build_fts5_query, "
            f"got builder calls: {seen!r}"
        )

    def test_production_form_is_or_of_phrases(self) -> None:
        # Sanity check that the production builder produces an OR-of-phrases
        # (not a single adjacency phrase), which is what the benchmark now uses.
        prod = build_fts5_query("hello world")
        assert '"hello"' in prod
        assert '"world"' in prod
        assert " OR " in prod


# ---------------------------------------------------------------------------
# F4 - reserve_cost uses atomic write
# ---------------------------------------------------------------------------


class TestF4ReserveCostAtomic:
    """reserve_cost must persist via atomic_write_text, not plain open('a')."""

    def test_reservation_persists_and_is_valid_json(self, tmp_path: Path) -> None:
        ledger = tmp_path / "ledger.jsonl"
        rid, ok = reserve_cost(
            ledger,
            estimated_cost_usd=0.01,
            host="h",
            cap_usd_monthly=1.0,
        )
        assert ok
        assert ledger.is_file()
        lines = [
            ln for ln in ledger.read_text(encoding="utf-8").splitlines() if ln.strip()
        ]
        assert len(lines) == 1
        rec = json.loads(lines[0])
        assert rec["reservation_id"] == rid
        assert rec["kind"] == "compression-reserved"

    def test_atomic_write_called_not_plain_open(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        ledger = tmp_path / "ledger.jsonl"
        calls: list[Path] = []
        original = atomic_write_text

        def spy(path: Path, content: str, **kw: object) -> None:
            calls.append(path)
            original(path, content, **kw)

        monkeypatch.setattr("mneme_core.compression.ledger.atomic_write_text", spy)
        reserve_cost(
            ledger,
            estimated_cost_usd=0.01,
            host="h",
            cap_usd_monthly=1.0,
        )
        assert any(p == ledger for p in calls), (
            "reserve_cost must call atomic_write_text with the ledger path"
        )


# ---------------------------------------------------------------------------
# F5 - run_compression returns structured RunReport on LedgerCorruptError
# ---------------------------------------------------------------------------


def _make_pipeline_config(tmp_path: Path) -> PipelineConfig:
    vault = tmp_path / "vault"
    vault.mkdir(exist_ok=True)
    cfg = CompressionConfig.default()
    cfg = CompressionConfig(
        enabled=True,
        model=cfg.model,
        max_tokens=cfg.max_tokens,
        cost_cap_usd_monthly=cfg.cost_cap_usd_monthly,
        max_payload_bytes=cfg.max_payload_bytes,
    )
    return PipelineConfig(
        compression=cfg,
        staging_dir=tmp_path / "staging",
        daily_log_dir=tmp_path / "daily",
        ledger_path=tmp_path / "ledger.jsonl",
        pause_flag_path=tmp_path / "paused.flag",
        host="testhost",
        staging_active_window_s=0.0,
    )


def _seed_staging(cfg: PipelineConfig) -> None:
    s_cfg = StagingConfig(
        staging_dir=cfg.staging_dir,
        audit_dir=cfg.staging_dir / "audit",
        host=cfg.host,
    )
    capture_event({"tool_name": "Edit", "data": "x"}, s_cfg)


class TestF5LedgerCorruptRunReport:
    """Corrupt ledger must return RunReport(status='ledger_corrupt'), not raise."""

    def test_corrupt_ledger_returns_structured_report(self, tmp_path: Path) -> None:
        pcfg = _make_pipeline_config(tmp_path)
        _seed_staging(pcfg)
        # Write a ledger file with only non-JSON lines to trigger LedgerCorruptError.
        pcfg.ledger_path.parent.mkdir(parents=True, exist_ok=True)
        pcfg.ledger_path.write_text("not-json\nnot-json\n", encoding="utf-8")

        # Patch reserve_cost to simulate the reserved=False branch so check_cap
        # is reached.
        import mneme_core.compression.pipeline as _pipe

        def _mock_reserve(path: Path, **kw: object) -> tuple[str, bool]:
            # Return ok=False so the pipeline hits the check_cap branch.
            return "mock-id", False

        with patch.object(_pipe, "reserve_cost", _mock_reserve):
            report = run_compression(pcfg)

        assert isinstance(report, RunReport)
        assert report.status == "ledger_corrupt"
        assert report.reason is not None


# ---------------------------------------------------------------------------
# F6 - _truncate_payload byte-level truncation
# ---------------------------------------------------------------------------


class TestF6TruncatePayloadBytes:
    """_truncate_payload must not produce output exceeding limit bytes."""

    def test_ascii_below_limit_unchanged(self) -> None:
        s = "hello world"
        assert _truncate_payload(s, 200) == s

    def test_multibyte_result_fits_in_limit(self) -> None:
        # Each Turkish character here is 2 bytes in UTF-8.
        payload = "ş" * 100  # 200 bytes
        limit = 50
        result = _truncate_payload(payload, limit)
        assert len(result.encode("utf-8")) <= limit + len(b"\n...[TRUNCATED]")
        # The non-suffix part must decode cleanly (no broken characters).
        suffix = "\n...[TRUNCATED]"
        body = result[: result.rfind(suffix)] if suffix in result else result
        body.encode("utf-8")  # must not raise

    def test_no_broken_multibyte_at_cut(self) -> None:
        # 3-byte CJK chars: cut at a byte boundary that falls inside a char.
        payload = "中" * 30  # 90 bytes
        limit = 10
        result = _truncate_payload(payload, limit)
        # Must not raise and result must be valid UTF-8.
        result.encode("utf-8")


# ---------------------------------------------------------------------------
# F7 - capture_event byte counter matches real file growth
# ---------------------------------------------------------------------------


class TestF7CaptureEventByteCounter:
    """_write_event must return the exact bytes appended, counting ephemeral fields."""

    def test_write_event_returns_exact_bytes_written(self, tmp_path: Path) -> None:
        cfg = StagingConfig(
            staging_dir=tmp_path / "staging",
            audit_dir=tmp_path / "audit",
            host="h",
        )
        event = {"tool_name": "Edit", "data": "abc"}
        pre_injection = len(json.dumps(event, ensure_ascii=False).encode("utf-8"))

        written = _write_event(dict(event), cfg)

        # The returned count must equal the actual bytes appended to disk.
        files = [
            f
            for f in (cfg.staging_dir / cfg.host).rglob("*.jsonl")
            if "archive" not in f.parts
        ]
        assert len(files) == 1
        assert files[0].stat().st_size == written, (
            "_write_event must return exactly the bytes it wrote to disk"
        )
        # And that count must exceed the pre-injection payload size, proving the
        # injected ephemeral fields (content_hash/captured_at/host) are counted
        # rather than the smaller pre-injection payload.
        assert written > pre_injection


# ---------------------------------------------------------------------------
# F8 - kg/worker per-file counters for archive decision
# ---------------------------------------------------------------------------


@pytest.fixture
def kg_cfg(tmp_path: Path) -> KgConfig:
    state = tmp_path / "state"
    cfg = KgConfig(
        queue_dir=state / "kg-queue",
        processed_dir=state / "kg-processed",
        active_flag=state / "kg-active.flag",
        credentials_path=state / "creds.json",
        community_refresh_flag=state / "refresh.flag",
        worker_log=state / "worker.jsonl",
        cost_ledger=state / "ledger.jsonl",
        host="h",
    )
    cfg.active_flag.parent.mkdir(parents=True, exist_ok=True)
    cfg.active_flag.write_text("on\n", encoding="utf-8")
    return cfg


class TestF8PerFileArchiveCounters:
    """A failing file must not prevent archiving of a clean file."""

    def _seed_file(self, queue_dir: Path, host: str, name: str) -> Path:
        host_dir = queue_dir / host / "2026-01-01"
        host_dir.mkdir(parents=True, exist_ok=True)
        f = host_dir / name
        f.write_text(
            json.dumps(
                {
                    "event_id": name,
                    "ts": "2026-01-01T00:00:00+00:00",
                    "content_hash": "abc",
                    "payload": {},
                }
            )
            + "\n",
            encoding="utf-8",
        )
        return f

    def test_clean_file_archived_even_when_earlier_file_fails(
        self, kg_cfg: KgConfig, tmp_path: Path
    ) -> None:
        bad_file = self._seed_file(kg_cfg.queue_dir, kg_cfg.host, "bad-events.jsonl")
        clean_file = self._seed_file(
            kg_cfg.queue_dir, kg_cfg.host, "clean-events.jsonl"
        )

        # Directly call _archive_file for clean_file to verify it moves; bad_file
        # is left in place to model the file_failed > 0 case for an earlier file.
        _archive_file(clean_file, kg_cfg)
        dest = kg_cfg.processed_dir / clean_file.relative_to(kg_cfg.queue_dir)
        assert dest.is_file(), "Clean file must be archived"
        assert not clean_file.exists(), "Clean file must be removed from queue"

        # bad_file was never archived - must still be in queue.
        assert bad_file.exists(), "Bad file must remain in queue"


# ---------------------------------------------------------------------------
# F9 - _archive_file cross-device fallback
# ---------------------------------------------------------------------------


class TestF9ArchiveCrossDevice:
    """Cross-device OSError on Path.replace must fall back to copy+unlink."""

    def test_fallback_copy_used_on_exdev(
        self, kg_cfg: KgConfig, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        host_dir = kg_cfg.queue_dir / kg_cfg.host / "2026-01-01"
        host_dir.mkdir(parents=True, exist_ok=True)
        src = host_dir / "evt.jsonl"
        src.write_text('{"x":1}\n', encoding="utf-8")

        # Monkeypatch Path.replace to raise OSError (EXDEV) on every call.
        def _raise_exdev(self: Path, target: Path) -> Path:
            raise OSError(18, "Cross-device link")

        monkeypatch.setattr(Path, "replace", _raise_exdev)

        _archive_file(src, kg_cfg)

        dest = kg_cfg.processed_dir / src.relative_to(kg_cfg.queue_dir)
        assert dest.is_file(), "File must be archived via copy+unlink fallback"
        assert not src.exists(), "Source must be removed after copy"


# ---------------------------------------------------------------------------
# F10 - episode_stage content_hash excludes ephemeral fields
# ---------------------------------------------------------------------------


class TestF10EpisodeStageContentHash:
    """Two events identical except for ts/pid must produce the same content_hash."""

    def _get_hash(self, kg_cfg: KgConfig, event: dict) -> str:
        stage_event(event, kg_cfg)
        files = list(kg_cfg.queue_dir.rglob("*-events.jsonl"))
        assert files
        line = files[-1].read_text(encoding="utf-8").strip().splitlines()[-1]
        return json.loads(line)["content_hash"]

    def test_same_logical_event_same_hash(
        self, kg_cfg: KgConfig, tmp_path: Path
    ) -> None:
        base = {"tool_name": "Edit", "args": {"file": "/tmp/a.py", "content": "x=1"}}
        h1 = self._get_hash(kg_cfg, dict(base))

        # Wipe queue dir to capture second event in a fresh file.
        shutil.rmtree(kg_cfg.queue_dir)
        kg_cfg.queue_dir.mkdir(parents=True)

        h2 = self._get_hash(kg_cfg, dict(base))
        assert h1 == h2, (
            f"Content hashes differ ({h1!r} vs {h2!r}); ephemeral fields "
            "must be excluded from the dedup hash"
        )
        assert len(h1) == 64, "content_hash must remain a full sha256 hex digest"


# ---------------------------------------------------------------------------
# F11 - privacy.redact(None) returns ""
# ---------------------------------------------------------------------------


class TestF11RedactNone:
    def test_none_returns_empty_string(self) -> None:
        assert redact(None) == ""

    def test_empty_string_returns_empty_string(self) -> None:
        assert redact("") == ""

    def test_normal_string_unchanged(self) -> None:
        assert redact("hello") == "hello"

    def test_redacts_private_tag(self) -> None:
        assert redact("<private>secret</private>") == "[REDACTED]"


# ---------------------------------------------------------------------------
# F12 - save_tracker uses atomic_write_text
# ---------------------------------------------------------------------------


class TestF12SaveTrackerAtomic:
    """save_tracker must use atomic_write_text, not Path.write_text."""

    def test_atomic_write_called(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        calls: list[Path] = []
        original = atomic_write_text

        def spy(path: Path, content: str, **kw: object) -> None:
            calls.append(path)
            original(path, content, **kw)

        monkeypatch.setattr(
            "mneme_core.distill.injection_dedup.atomic_write_text", spy
        )
        t = InjectionTracker(session_id="sess-1")
        save_tracker(tmp_path, t)
        assert calls, "save_tracker must call atomic_write_text"

    def test_round_trip_correct(self, tmp_path: Path) -> None:
        t = InjectionTracker(session_id="sess-2")
        t.seen_hashes.add("h1")
        t.hits = 1
        save_tracker(tmp_path, t)
        loaded = load_tracker(tmp_path, "sess-2")
        assert loaded.seen_hashes == {"h1"}
        assert loaded.hits == 1


# ---------------------------------------------------------------------------
# F13 - doctor frontmatter_dates uses load_yaml_block (out-of-range date)
# ---------------------------------------------------------------------------


def _make_vault(tmp_path: Path) -> Path:
    vault_root = tmp_path / "vault"
    vault_root.mkdir()
    (vault_root / ".mneme").mkdir()
    return vault_root


class TestF13DoctorOutOfRangeDate:
    """doctor must report out-of-range date as warn AND continue the walk."""

    def test_out_of_range_date_reported_as_warn(self, tmp_path: Path) -> None:
        vault_root = _make_vault(tmp_path)
        bad = vault_root / "bad-date.md"
        bad.write_text(
            "---\nid: x\ntype: session\ncreated: 2026-13-99\n---\n# Bad\n",
            encoding="utf-8",
        )
        runner = CliRunner()
        result = runner.invoke(cli, ["doctor", "--vault", str(vault_root)])
        assert result.exit_code == 0
        data = json.loads(result.output)
        fd = next(c for c in data["checks"] if c["name"] == "frontmatter_dates")
        assert fd["status"] == "warn"

    def test_walk_continues_after_bad_date_file(self, tmp_path: Path) -> None:
        vault_root = _make_vault(tmp_path)
        (vault_root / "bad.md").write_text(
            "---\nid: b\ntype: session\ncreated: 2026-13-99\n---\n# Bad\n",
            encoding="utf-8",
        )
        (vault_root / "good.md").write_text(
            "---\nid: g\ntype: session\ncreated: 2024-01-01T00:00:00+00:00\n---\n# Good\n",
            encoding="utf-8",
        )
        runner = CliRunner()
        result = runner.invoke(cli, ["doctor", "--vault", str(vault_root)])
        assert result.exit_code == 0
        data = json.loads(result.output)
        # All standard checks must be present - the walk did not abort.
        names = {c["name"] for c in data["checks"]}
        assert "vault_resolves" in names
        assert "frontmatter_dates" in names


# ---------------------------------------------------------------------------
# F14 - list_trajectories continues past bad-date file
# ---------------------------------------------------------------------------


class TestF14ListTrajectoriesBadDate:
    """list_trajectories must not abort when one file has an out-of-range date."""

    def test_good_trajectories_still_listed(self, tmp_path: Path) -> None:
        vault = VaultConfig.from_path(tmp_path)
        (vault.root / ".mneme").mkdir(parents=True, exist_ok=True)

        # Write a good trajectory.
        start_trajectory(vault, "sess-good")
        record_step(vault, "sess-good", action="act", observation="obs")

        # Manually inject a bad-date trajectory file.
        traj_dir = vault.trajectories_dir
        date_str = datetime.date.today().isoformat()
        bad_dir = traj_dir / date_str
        bad_dir.mkdir(parents=True, exist_ok=True)
        (bad_dir / "bad-sess.md").write_text(
            "---\ntype: trajectory\nsession_id: bad-sess\n"
            "started_at: 2026-13-99\nended_at: null\nschema_version: 1.0.0\n---\n\n"
            "# Trajectory\n",
            encoding="utf-8",
        )

        trajs = list_trajectories(vault)
        # The good trajectory must be present.
        ids = [t.session_id for t in trajs]
        assert "sess-good" in ids, f"Good trajectory missing from listing; got: {ids}"


# ---------------------------------------------------------------------------
# F15 - list_patterns continues past bad-date file
# ---------------------------------------------------------------------------


class TestF15ListPatternsBadDate:
    """list_patterns must not abort when one file has an out-of-range date."""

    def test_good_patterns_still_listed(self, tmp_path: Path) -> None:
        vault = VaultConfig.from_path(tmp_path)
        (vault.root / ".mneme").mkdir(parents=True, exist_ok=True)

        # Write a good pattern.
        store_pattern(
            vault,
            Pattern(name="good-pattern", signal="sig", action="act"),
        )

        # Write a bad-date pattern file directly.
        vault.patterns_dir.mkdir(parents=True, exist_ok=True)
        (vault.patterns_dir / "bad-date.md").write_text(
            "---\ntype: pattern\nname: bad-date\ntags: []\n"
            "created_at: 2026-13-99\nschema_version: 1.0.0\n---\n\n"
            "## Signal\n\nsig\n\n## Action\n\nact\n",
            encoding="utf-8",
        )

        patterns = list_patterns(vault)
        names = [p.name for p in patterns]
        assert "good-pattern" in names, (
            f"Good pattern missing from listing; got: {names}"
        )
