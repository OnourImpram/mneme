"""Cross-platform exclusive file lock primitive.

Used by:

  - the Stop hook to serialize daily session log appends across
    concurrent stop events,
  - the compression cost ledger to serialize the reserve / settle /
    rollback transaction so concurrent workers cannot overspend the
    monthly cap.

The lock is OS-mandatory on Windows (``msvcrt.locking``) and advisory
on POSIX (``fcntl.flock``). Both are honored by every process that
takes the lock through this helper.

The lockfile is independent of the data file: this avoids the
common ``flock(open("w"))`` race where the lock acquisition itself
truncates the data. The lockfile is created on first acquisition
and left in place across runs (it is empty and harmless).
"""

from __future__ import annotations

import sys
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import IO

_DEFAULT_TIMEOUT_S = 5.0
_DEFAULT_POLL_S = 0.05


@contextmanager
def file_lock(
    lock_path: Path,
    *,
    timeout_s: float = _DEFAULT_TIMEOUT_S,
    poll_s: float = _DEFAULT_POLL_S,
) -> Iterator[None]:
    """Block until ``lock_path`` is held exclusively, release on exit.

    ``timeout_s`` caps how long the caller waits to acquire. A
    ``TimeoutError`` is raised when the deadline expires so the
    caller can decide whether to skip the operation or escalate.

    The lock is released on context exit and on any exception that
    propagates through the ``with`` block.
    """
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    fp = lock_path.open("a+b")
    try:
        _acquire(fp, timeout_s=timeout_s, poll_s=poll_s)
        try:
            yield
        finally:
            _release(fp)
    finally:
        fp.close()


def _acquire(fp: IO[bytes], *, timeout_s: float, poll_s: float) -> None:
    if sys.platform == "win32":
        import msvcrt

        deadline = time.monotonic() + timeout_s
        while True:
            try:
                fp.seek(0)
                msvcrt.locking(fp.fileno(), msvcrt.LK_NBLCK, 1)
                return
            except OSError:
                if time.monotonic() > deadline:
                    raise TimeoutError(
                        f"Could not acquire lock on {fp.name} within {timeout_s}s"
                    ) from None
                time.sleep(poll_s)
    else:
        import fcntl

        deadline = time.monotonic() + timeout_s
        while True:
            try:
                fcntl.flock(fp, fcntl.LOCK_EX | fcntl.LOCK_NB)
                return
            except BlockingIOError:
                if time.monotonic() > deadline:
                    raise TimeoutError(
                        f"Could not acquire lock on {fp.name} within {timeout_s}s"
                    ) from None
                time.sleep(poll_s)


def _release(fp: IO[bytes]) -> None:
    try:
        if sys.platform == "win32":
            import msvcrt

            fp.seek(0)
            try:
                msvcrt.locking(fp.fileno(), msvcrt.LK_UNLCK, 1)
            except OSError:
                # Kernel reclaims the lock on close.
                pass
        else:
            import fcntl

            fcntl.flock(fp, fcntl.LOCK_UN)
    except Exception:
        # Best-effort release; lock drops on close in any case.
        pass
