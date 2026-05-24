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

import os
import tempfile
from pathlib import Path


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
        try:
            os.close(dir_fd)
        except OSError:
            pass


def atomic_write_text(path: Path, content: str, encoding: str = "utf-8") -> None:
    """Write ``content`` to ``path`` atomically.

    Args:
        path: destination file path. Parent directory is created if missing.
        content: text content to write.
        encoding: text encoding for the write. Defaults to UTF-8.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        prefix=path.name + ".",
        suffix=".tmp",
        dir=str(path.parent),
    )
    try:
        with os.fdopen(fd, "w", encoding=encoding, newline="\n") as fp:
            fp.write(content)
            fp.flush()
            os.fsync(fp.fileno())
        Path(tmp_name).replace(path)
        _fsync_dir(path.parent)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except FileNotFoundError:
            pass
        raise
