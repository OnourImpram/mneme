"""Tests for the per-session injection-dedup tracker."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from mneme_core.distill.injection_dedup import (
    InjectionTracker,
    has_injected,
    load_tracker,
    mark_injected,
    save_tracker,
)


class TestBasicOperations:
    def test_fresh_tracker_is_empty(self) -> None:
        t = InjectionTracker(session_id="s-1")
        assert has_injected(t, "h1") is False
        assert t.hits == 0
        assert t.skips == 0

    def test_mark_then_has(self) -> None:
        t = InjectionTracker(session_id="s-1")
        mark_injected(t, "abc123")
        assert has_injected(t, "abc123") is True
        assert t.hits == 1
        assert t.skips == 0

    def test_double_mark_increments_skips(self) -> None:
        t = InjectionTracker(session_id="s-1")
        mark_injected(t, "x")
        mark_injected(t, "x")
        assert t.hits == 1
        assert t.skips == 1


class TestPersistence:
    def test_save_then_load_round_trips(self, tmp_path: Path) -> None:
        t = InjectionTracker(session_id="s-1")
        mark_injected(t, "h1")
        mark_injected(t, "h2")
        mark_injected(t, "h1")  # one skip
        assert save_tracker(tmp_path, t) is True

        loaded = load_tracker(tmp_path, "s-1")
        assert loaded.session_id == "s-1"
        assert loaded.seen_hashes == {"h1", "h2"}
        assert loaded.hits == 2
        assert loaded.skips == 1

    def test_missing_file_yields_empty(self, tmp_path: Path) -> None:
        t = load_tracker(tmp_path, "never-existed")
        assert t.session_id == "never-existed"
        assert t.seen_hashes == set()
        assert t.hits == 0

    def test_malformed_file_yields_empty(self, tmp_path: Path) -> None:
        tracker_dir = tmp_path / "injection-tracker"
        tracker_dir.mkdir(parents=True)
        (tracker_dir / "s-bad.json").write_text("not-json", encoding="utf-8")
        t = load_tracker(tmp_path, "s-bad")
        assert t.seen_hashes == set()

    def test_unsafe_session_id_sanitized(self, tmp_path: Path) -> None:
        # Slashes and colons should not break the filename.
        t = InjectionTracker(session_id="../../etc/passwd")
        assert save_tracker(tmp_path, t) is True
        # File created somewhere under tmp_path with a sanitized name.
        files = list((tmp_path / "injection-tracker").glob("*.json"))
        assert len(files) == 1
        # No path traversal artifact in the resulting file path.
        assert ".." not in files[0].name

    def test_save_failure_returns_false(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        t = InjectionTracker(session_id="s-1")

        def _boom(*_a: object, **_kw: object) -> None:
            raise OSError("disk full")

        monkeypatch.setattr(Path, "mkdir", _boom)
        assert save_tracker(tmp_path, t) is False
