"""Unit tests for the atomic file write helper."""

from __future__ import annotations

import contextlib
import os
from pathlib import Path
from unittest.mock import patch

import pytest

from mneme_core.vault import atomic_write as aw_module
from mneme_core.vault.atomic_write import (
    AtomicWritePathError,
    _fsync_dir,
    atomic_write_bytes,
    atomic_write_text,
)


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

    @pytest.mark.skipif(os.name == "nt", reason="symlink creation needs elevation")
    def test_guarded_write_rejects_symlink_escape(self, tmp_path: Path) -> None:
        vault = tmp_path / "vault"
        outside = tmp_path / "outside"
        vault.mkdir()
        outside.mkdir()
        escape = vault / "escape"
        escape.symlink_to(outside, target_is_directory=True)

        with pytest.raises(AtomicWritePathError):
            atomic_write_text(
                escape / "leaked.md", "secret", vault_root=vault
            )

        assert not (outside / "leaked.md").exists()

    def test_parent_identity_change_fails_before_content_write(
        self, tmp_path: Path
    ) -> None:
        target = tmp_path / "out.md"
        original = aw_module._directory_identity
        calls = 0

        def changing_identity(directory: Path) -> tuple[int, int, Path]:
            nonlocal calls
            calls += 1
            dev, ino, resolved = original(directory)
            if calls > 1:
                return dev, ino + 1, resolved
            return dev, ino, resolved

        with (
            patch.object(aw_module, "_directory_identity", changing_identity),
            pytest.raises(AtomicWritePathError),
        ):
            atomic_write_text(target, "secret", vault_root=tmp_path)

        assert not target.exists()
        assert not any(".tmp" in entry.name for entry in tmp_path.iterdir())


class TestAtomicWriteBytes:
    def test_creates_and_overwrites_binary_file(self, tmp_path: Path) -> None:
        target = tmp_path / "artifact.bin"
        atomic_write_bytes(target, b"first\x00payload", vault_root=tmp_path)
        atomic_write_bytes(target, b"second\xffpayload", vault_root=tmp_path)
        assert target.read_bytes() == b"second\xffpayload"
        assert not any(".tmp" in entry.name for entry in tmp_path.iterdir())

    @pytest.mark.skipif(os.name == "nt", reason="symlink creation needs elevation")
    def test_guarded_binary_write_rejects_symlink_escape(self, tmp_path: Path) -> None:
        vault = tmp_path / "vault"
        outside = tmp_path / "outside"
        vault.mkdir()
        outside.mkdir()
        escape = vault / "escape"
        escape.symlink_to(outside, target_is_directory=True)
        with pytest.raises(AtomicWritePathError):
            atomic_write_bytes(escape / "leaked.bin", b"secret", vault_root=vault)
        assert not (outside / "leaked.bin").exists()


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
        # Best-effort dir fsync: the rename has already committed
        # before the helper runs, so the file is on disk regardless
        # of whether the helper raised.
        with (
            patch.object(aw_module, "_fsync_dir", side_effect=OSError("simulated")),
            contextlib.suppress(OSError),
        ):
            atomic_write_text(target, "y")
        assert target.is_file()
        assert target.read_text(encoding="utf-8") == "y"
