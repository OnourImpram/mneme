"""Atomic file write helper.

POSIX ``rename(2)`` is atomic on the same filesystem. Windows NTFS
``MoveFileEx`` with ``MOVEFILE_REPLACE_EXISTING`` is atomic. CPython 3.11+
``Path.replace()`` exercises both correctly. We write to a temp file in
the same directory as the destination to keep the rename on one
filesystem, then ``fsync`` before the rename.

On POSIX we also ``fsync`` the parent directory after the rename so the
directory entry change is durable. Windows does not expose directory
fsync, so the dir-fsync step is skipped there (NTFS journals the
rename, so durability is still guaranteed by the OS).

Failure modes handled:

- Parent directory missing: created with ``parents=True`` before writing.
- Partial write: temp file is unlinked on any exception during write or
  rename, never leaving stale ``.tmp`` files behind on success.
- Directory fsync failure: tolerated; the rename has already succeeded
  and a missed dir-fsync only weakens durability on power loss within
  a small window.
"""

from __future__ import annotations

import contextlib
import os
import tempfile
from pathlib import Path


class AtomicWritePathError(OSError):
    """Raised when a guarded atomic write cannot prove path stability."""


def _assert_within_root(root: Path, target: Path) -> None:
    """Require *target* to resolve inside an existing trusted root."""
    try:
        root_resolved = root.resolve(strict=True)
        target_resolved = target.resolve(strict=False)
    except (OSError, RuntimeError) as exc:
        raise AtomicWritePathError(f"cannot resolve guarded write path: {exc}") from exc
    if target_resolved != root_resolved and not target_resolved.is_relative_to(
        root_resolved
    ):
        raise AtomicWritePathError(
            f'path "{target}" resolves outside guarded root "{root_resolved}"'
        )


def _directory_identity(directory: Path) -> tuple[int, int, Path]:
    """Return a stable filesystem identity for a directory."""
    stat = directory.stat()
    if not directory.is_dir():
        raise AtomicWritePathError(f'atomic-write parent "{directory}" is not a directory')
    return stat.st_dev, stat.st_ino, directory.resolve(strict=True)


def _assert_directory_identity(
    directory: Path,
    expected: tuple[int, int, Path],
    *,
    vault_root: Path | None,
) -> None:
    if vault_root is not None:
        _assert_within_root(vault_root, directory)
    if _directory_identity(directory) != expected:
        raise AtomicWritePathError(
            f'atomic-write parent "{directory}" changed during the operation'
        )


def _fsync_dir(directory: Path) -> None:
    """Best-effort directory fsync. POSIX only; silent no-op elsewhere."""
    if os.name == "nt":
        return
    try:
        dir_fd = os.open(str(directory), os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(dir_fd)
    except OSError:
        pass
    finally:
        with contextlib.suppress(OSError):
            os.close(dir_fd)


def atomic_write_text(
    path: Path,
    content: str,
    encoding: str = "utf-8",
    *,
    vault_root: Path | None = None,
) -> None:
    """Write ``content`` to ``path`` atomically.

    Args:
        path: destination file path. Parent directory is created if missing.
        content: text content to write.
        encoding: text encoding for the write. Defaults to UTF-8.
        vault_root: optional trusted root. When set, containment and parent
            identity are revalidated before content is written and renamed.
    """
    if vault_root is not None:
        # Validate before mkdir so an escaping path cannot create directories.
        _assert_within_root(vault_root, path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if vault_root is not None:
        _assert_within_root(vault_root, path)
    parent_identity = _directory_identity(path.parent)
    fd, tmp_name = tempfile.mkstemp(
        prefix=path.name + ".",
        suffix=".tmp",
        dir=str(path.parent),
    )
    open_fd: int | None = fd
    try:
        # mkstemp has created only an empty O_EXCL file. Refuse before writing
        # content if a symlink or reparse-point swap changed the parent.
        _assert_directory_identity(
            path.parent, parent_identity, vault_root=vault_root
        )
        with os.fdopen(fd, "w", encoding=encoding, newline="\n") as fp:
            open_fd = None
            fp.write(content)
            fp.flush()
            os.fsync(fp.fileno())
        _assert_directory_identity(
            path.parent, parent_identity, vault_root=vault_root
        )
        Path(tmp_name).replace(path)
        _fsync_dir(path.parent)
    except BaseException:
        if open_fd is not None:
            with contextlib.suppress(OSError):
                os.close(open_fd)
            open_fd = None
        with contextlib.suppress(FileNotFoundError):
            os.unlink(tmp_name)
        raise
    finally:
        if open_fd is not None:
            with contextlib.suppress(OSError):
                os.close(open_fd)


def atomic_write_bytes(
    path: Path,
    content: bytes,
    *,
    vault_root: Path | None = None,
) -> None:
    """Atomically write bytes with the same guarded contract as text."""
    if vault_root is not None:
        _assert_within_root(vault_root, path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if vault_root is not None:
        _assert_within_root(vault_root, path)
    parent_identity = _directory_identity(path.parent)
    fd, tmp_name = tempfile.mkstemp(
        prefix=path.name + ".",
        suffix=".tmp",
        dir=str(path.parent),
    )
    open_fd: int | None = fd
    try:
        _assert_directory_identity(
            path.parent, parent_identity, vault_root=vault_root
        )
        with os.fdopen(fd, "wb") as fp:
            open_fd = None
            fp.write(content)
            fp.flush()
            os.fsync(fp.fileno())
        _assert_directory_identity(
            path.parent, parent_identity, vault_root=vault_root
        )
        Path(tmp_name).replace(path)
        _fsync_dir(path.parent)
    except BaseException:
        if open_fd is not None:
            with contextlib.suppress(OSError):
                os.close(open_fd)
            open_fd = None
        with contextlib.suppress(FileNotFoundError):
            os.unlink(tmp_name)
        raise
    finally:
        if open_fd is not None:
            with contextlib.suppress(OSError):
                os.close(open_fd)
