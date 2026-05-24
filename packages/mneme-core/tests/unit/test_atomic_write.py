"""Unit tests for the atomic file write helper."""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

import pytest

from mneme_core.vault import atomic_write as aw_module
from mneme_core.vault.atomic_write import _fsync_dir, atomic_write_text


class TestAtomicWriteText:
    def test_creates_file(self, tmp_path: Path) -> None:
        target = tmp_path / "out.md"
        atomic_write_text(target, "hello")
        assert target.read_text(encoding="utf-8") == "hello"

    def test_creates_parent_dirs(self, tmp_path: Path) -> None:
        target = tmp_path / "a" / "b" / "c.md"
        atomic_write_text(target, "deep")
        assert target.read_text(encoding="utf-8") == "deep"

    def test_overwrites_existing(self, tmp_path: Path) -> None:
        target = tmp_path / "out.md"
        target.write_text("old", encoding="utf-8")
        atomic_write_text(target, "new")
        assert target.read_text(encoding="utf-8") == "new"

    def test_no_temp_file_remains_on_success(self, tmp_path: Path) -> None:
        target = tmp_path / "out.md"
        atomic_write_text(target, "x")
        leftover = [p for p in tmp_path.iterdir() if ".tmp" in p.name]
        assert leftover == []

    def test_unicode_content(self, tmp_path: Path) -> None:
        target = tmp_path / "out.md"
        atomic_write_text(target, "kıyaslama öğrenci")
        assert target.read_text(encoding="utf-8") == "kıyaslama öğrenci"

    def test_empty_content(self, tmp_path: Path) -> None:
        target = tmp_path / "out.md"
        atomic_write_text(target, "")
        assert target.read_text(encoding="utf-8") == ""

    def test_uses_lf_newlines(self, tmp_path: Path) -> None:
        target = tmp_path / "out.md"
        atomic_write_text(target, "line1\nline2\n")
        raw = target.read_bytes()
        assert b"\r\n" not in raw
        assert raw == b"line1\nline2\n"


class TestDirectoryFsync:
    """Phase J Day 6: parent directory must be fsynced after rename on POSIX.

    VAULT.md L99-104 documents this as part of the atomic-write
    contract. Prior to Day 6 the helper fsynced the file before
    rename but never the parent directory, so a power loss between
    rename and the next filesystem sync could lose the directory
    entry change on some POSIX filesystems even though the file
    content survived.
    """

    @pytest.mark.skipif(os.name == "nt", reason="POSIX-only dir fsync contract")
    def test_dir_fsync_called_once_on_posix(self, tmp_path: Path) -> None:
        target = tmp_path / "out.md"
        with patch.object(
            aw_module, "_fsync_dir", wraps=aw_module._fsync_dir
        ) as spy:
            atomic_write_text(target, "x")
        spy.assert_called_once()
        assert spy.call_args.args[0] == target.parent

    def test_dir_fsync_helper_runs_without_error(self, tmp_path: Path) -> None:
        """``_fsync_dir`` must not raise on any platform for a valid dir."""
        _fsync_dir(tmp_path)

    def test_dir_fsync_tolerates_missing_directory(self, tmp_path: Path) -> None:
        """A non-existent dir path must be silently tolerated."""
        _fsync_dir(tmp_path / "does-not-exist")

    def test_write_survives_dir_fsync_failure(self, tmp_path: Path) -> None:
        """A simulated dir-fsync OSError must not corrupt the rename outcome."""
        target = tmp_path / "out.md"
        with patch.object(
            aw_module, "_fsync_dir", side_effect=OSError("simulated")
        ):
            try:
                atomic_write_text(target, "y")
            except OSError:
                # Best-effort dir fsync: the rename has already
                # committed before the helper runs, so the file is on
                # disk regardless of whether the helper raised.
                pass
        assert target.is_file()
        assert target.read_text(encoding="utf-8") == "y"
