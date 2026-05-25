"""Unit tests for the cross-platform file lock primitive.

Coverage targets
----------------
(a) Basic acquire-then-release via the context manager: lock file is
    created, the body executes, and the lock is cleanly released on
    exit so a second acquisition immediately succeeds.

(b) Sequential re-use (not concurrent re-entrancy — the API is *not*
    a re-entrant lock, just a plain exclusive lock): after one ``with``
    block finishes the same path can be locked again without error.

(c) Timeout path: hold the lock in a child *process* (so the OS-level
    mandatory lock is genuinely held by a different entity) and assert
    that a second acquisition with a very short ``timeout_s`` raises
    ``TimeoutError`` before the deadline expires.

Windows vs POSIX
----------------
On Windows ``msvcrt.locking`` is a per-process mandatory lock; two
*threads* within the same process that each call ``open()`` get
independent C-runtime file descriptors, but the Windows CRT only
enforces ``LK_NBLCK`` contention between different *processes*.  For
the timeout test we therefore use ``multiprocessing`` to spin up a
child process that holds the lock, then attempt acquisition from the
parent.  This correctly exercises the ``msvcrt`` branch on Windows and
the ``fcntl`` branch on POSIX.
"""

from __future__ import annotations

import multiprocessing
import multiprocessing.synchronize
import sys
import time
from pathlib import Path

import pytest

from mneme_core.vault.file_lock import _acquire, _release, file_lock

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_POLL_S = 0.01  # fast poll so tests don't drag


def _hold_lock(
    lock_path: Path,
    duration_s: float,
    ready_event: multiprocessing.synchronize.Event,
    *,
    timeout_s: float = 5.0,
) -> None:
    """Child-process target: acquire the lock, signal ready, hold, release."""
    with file_lock(lock_path, timeout_s=timeout_s, poll_s=_POLL_S):
        ready_event.set()
        time.sleep(duration_s)


# ---------------------------------------------------------------------------
# (a) Basic acquire and release
# ---------------------------------------------------------------------------


class TestBasicAcquireRelease:
    def test_lock_file_is_created(self, tmp_path: Path) -> None:
        lock_path = tmp_path / "test.lock"
        with file_lock(lock_path, timeout_s=2.0, poll_s=_POLL_S):
            assert lock_path.is_file()

    def test_body_executes_inside_context(self, tmp_path: Path) -> None:
        lock_path = tmp_path / "test.lock"
        executed: list[bool] = []
        with file_lock(lock_path, timeout_s=2.0, poll_s=_POLL_S):
            executed.append(True)
        assert executed == [True]

    def test_lock_file_remains_after_release(self, tmp_path: Path) -> None:
        """The lock *file* is intentionally left on disk between runs."""
        lock_path = tmp_path / "test.lock"
        with file_lock(lock_path, timeout_s=2.0, poll_s=_POLL_S):
            pass
        # File stays; it is empty and harmless (documented behavior).
        assert lock_path.is_file()

    def test_exception_in_body_releases_lock(self, tmp_path: Path) -> None:
        """An exception propagating through the ``with`` block must still
        release the lock so a subsequent acquisition succeeds."""
        lock_path = tmp_path / "test.lock"
        with (
            pytest.raises(ValueError, match="boom"),
            file_lock(lock_path, timeout_s=2.0, poll_s=_POLL_S),
        ):
            raise ValueError("boom")

        # Lock must be released; re-acquisition with a short timeout must work.
        with file_lock(lock_path, timeout_s=0.5, poll_s=_POLL_S):
            pass

    def test_parent_dirs_created(self, tmp_path: Path) -> None:
        lock_path = tmp_path / "deep" / "nested" / "dir" / "x.lock"
        with file_lock(lock_path, timeout_s=2.0, poll_s=_POLL_S):
            assert lock_path.is_file()


# ---------------------------------------------------------------------------
# (b) Sequential re-use (non-concurrent re-entrancy)
# ---------------------------------------------------------------------------


class TestSequentialReuse:
    def test_second_acquisition_succeeds_after_first_exits(
        self, tmp_path: Path
    ) -> None:
        lock_path = tmp_path / "seq.lock"
        with file_lock(lock_path, timeout_s=2.0, poll_s=_POLL_S):
            pass
        # Second acquisition on the same path must succeed without error.
        with file_lock(lock_path, timeout_s=2.0, poll_s=_POLL_S):
            pass

    def test_multiple_sequential_acquisitions(self, tmp_path: Path) -> None:
        lock_path = tmp_path / "multi.lock"
        for _ in range(5):
            with file_lock(lock_path, timeout_s=2.0, poll_s=_POLL_S):
                pass


# ---------------------------------------------------------------------------
# (c) Timeout path — cross-process contention
# ---------------------------------------------------------------------------


class TestTimeoutPath:
    @pytest.mark.skipif(
        sys.platform != "win32" and sys.platform not in ("linux", "darwin"),
        reason="Only tested on Win32 and POSIX",
    )
    def test_timeout_raises_when_lock_held_by_other_process(
        self, tmp_path: Path
    ) -> None:
        """A second acquisition must raise ``TimeoutError`` within the
        declared ``timeout_s`` when the lock is held by another process.

        We measure wall time so the test is self-validating: the
        ``TimeoutError`` must arrive *before* 3 × ``timeout_s`` to
        confirm the deadline is being respected (generous upper bound to
        avoid flakiness on slow CI).
        """
        lock_path = tmp_path / "timeout.lock"
        timeout_s = 0.2  # short but not so short it races on slow CI

        ctx = multiprocessing.get_context("spawn")
        ready: multiprocessing.synchronize.Event = ctx.Event()
        child = ctx.Process(
            target=_hold_lock,
            args=(lock_path, 5.0, ready),  # child holds for 5 s
        )
        child.start()
        try:
            signalled = ready.wait(timeout=10.0)
            assert signalled, "Child process did not acquire lock in time"

            t0 = time.monotonic()
            with (
                pytest.raises(TimeoutError),
                file_lock(lock_path, timeout_s=timeout_s, poll_s=_POLL_S),
            ):
                pass  # pragma: no cover
            elapsed = time.monotonic() - t0

            # Must not return too quickly (< half the timeout) — that would
            # mean the lock wasn't actually contended.
            assert elapsed >= timeout_s * 0.4, (
                f"TimeoutError raised too fast ({elapsed:.3f}s < {timeout_s * 0.4:.3f}s); "
                "was the lock actually held?"
            )
            # Must not take dramatically longer than the timeout.
            assert elapsed < timeout_s * 10, (
                f"TimeoutError took too long ({elapsed:.3f}s); deadline not respected"
            )
        finally:
            child.terminate()
            child.join(timeout=5.0)

    def test_timeout_error_message_contains_path(self, tmp_path: Path) -> None:
        """The ``TimeoutError`` message must include the lock file path so
        operators can identify which lock is contended."""
        lock_path = tmp_path / "msg.lock"
        timeout_s = 0.2

        ctx = multiprocessing.get_context("spawn")
        ready: multiprocessing.synchronize.Event = ctx.Event()
        child = ctx.Process(
            target=_hold_lock,
            args=(lock_path, 5.0, ready),
        )
        child.start()
        try:
            signalled = ready.wait(timeout=10.0)
            assert signalled, "Child process did not acquire lock in time"

            with (
                pytest.raises(TimeoutError, match=str(lock_path.name)),
                file_lock(lock_path, timeout_s=timeout_s, poll_s=_POLL_S),
            ):
                pass  # pragma: no cover
        finally:
            child.terminate()
            child.join(timeout=5.0)

    def test_zero_timeout_raises_immediately_when_held(
        self, tmp_path: Path
    ) -> None:
        """``timeout_s=0`` must raise ``TimeoutError`` on the very first
        failed attempt (the ``time.monotonic() > deadline`` check fires
        immediately after the initial poll sleep)."""
        lock_path = tmp_path / "zero.lock"

        ctx = multiprocessing.get_context("spawn")
        ready: multiprocessing.synchronize.Event = ctx.Event()
        child = ctx.Process(
            target=_hold_lock,
            args=(lock_path, 5.0, ready),
        )
        child.start()
        try:
            signalled = ready.wait(timeout=10.0)
            assert signalled, "Child process did not acquire lock in time"

            t0 = time.monotonic()
            with (
                pytest.raises(TimeoutError),
                file_lock(lock_path, timeout_s=0.0, poll_s=_POLL_S),
            ):
                pass  # pragma: no cover
            elapsed = time.monotonic() - t0
            # With timeout_s=0 and poll_s=0.01 the loop should fire within
            # a very short window (generous upper bound: 2 s for slow CI).
            assert elapsed < 2.0, (
                f"Zero-timeout acquisition took {elapsed:.3f}s — too slow"
            )
        finally:
            child.terminate()
            child.join(timeout=5.0)


# ---------------------------------------------------------------------------
# (d) Branch coverage via injected fakes
# ---------------------------------------------------------------------------
#
# The real ``_acquire`` / ``_release`` only ever run one OS branch per host:
# the ``msvcrt`` branch on Windows, the ``fcntl`` branch on POSIX. The dormant
# branch would ship a platform-only lock bug undetected even though both
# platforms are in the support matrix. These tests drive *both* branches on
# *any* host by monkeypatching ``sys.platform`` and injecting a fake lock
# module into ``sys.modules`` (no real Windows or POSIX runner required), and
# assert the exact lock-call sequence and argument values.


class _FakeMsvcrt:
    """Stand-in for the ``msvcrt`` module used by the win32 lock branch."""

    LK_NBLCK = 2
    LK_UNLCK = 0

    def __init__(
        self, *, lock_should_fail: bool = False, unlock_should_fail: bool = False
    ) -> None:
        self.calls: list[tuple[int, int, int]] = []
        self._lock_should_fail = lock_should_fail
        self._unlock_should_fail = unlock_should_fail

    def locking(self, fd: int, mode: int, nbytes: int) -> None:
        self.calls.append((fd, mode, nbytes))
        if mode == self.LK_NBLCK and self._lock_should_fail:
            raise OSError("region already locked")
        if mode == self.LK_UNLCK and self._unlock_should_fail:
            raise OSError("unlock failed")


class _FakeFcntl:
    """Stand-in for the ``fcntl`` module used by the POSIX lock branch."""

    LOCK_EX = 2
    LOCK_NB = 4
    LOCK_UN = 8

    def __init__(
        self, *, lock_should_block: bool = False, unlock_should_fail: bool = False
    ) -> None:
        self.calls: list[int] = []
        self._lock_should_block = lock_should_block
        self._unlock_should_fail = unlock_should_fail

    def flock(self, fp: object, operation: int) -> None:
        self.calls.append(operation)
        if operation == (self.LOCK_EX | self.LOCK_NB) and self._lock_should_block:
            raise BlockingIOError("would block")
        if operation == self.LOCK_UN and self._unlock_should_fail:
            raise OSError("unlock failed")


class TestWin32BranchInjected:
    """Cover the ``msvcrt`` acquire/release branch on any host."""

    def test_acquire_success_locks_one_byte(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(sys, "platform", "win32")
        fake = _FakeMsvcrt()
        monkeypatch.setitem(sys.modules, "msvcrt", fake)
        fp = open(tmp_path / "w.lock", "a+b")  # noqa: SIM115
        try:
            fd = fp.fileno()
            _acquire(fp, timeout_s=1.0, poll_s=_POLL_S)
            assert fake.calls == [(fd, _FakeMsvcrt.LK_NBLCK, 1)]
        finally:
            fp.close()

    def test_acquire_times_out_and_retries(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(sys, "platform", "win32")
        fake = _FakeMsvcrt(lock_should_fail=True)
        monkeypatch.setitem(sys.modules, "msvcrt", fake)
        fp = open(tmp_path / "w.lock", "a+b")  # noqa: SIM115
        try:
            t0 = time.monotonic()
            with pytest.raises(TimeoutError, match="w.lock"):
                _acquire(fp, timeout_s=0.2, poll_s=_POLL_S)
            elapsed = time.monotonic() - t0
            assert elapsed >= 0.2 * 0.4
            assert elapsed < 0.2 * 20  # generous upper bound for slow CI
            # The loop retried the non-blocking lock at least twice.
            assert len(fake.calls) >= 2
            assert all(mode == _FakeMsvcrt.LK_NBLCK for _, mode, _ in fake.calls)
        finally:
            fp.close()

    def test_release_unlocks_one_byte(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(sys, "platform", "win32")
        fake = _FakeMsvcrt()
        monkeypatch.setitem(sys.modules, "msvcrt", fake)
        fp = open(tmp_path / "w.lock", "a+b")  # noqa: SIM115
        try:
            fd = fp.fileno()
            _release(fp)
            assert fake.calls == [(fd, _FakeMsvcrt.LK_UNLCK, 1)]
        finally:
            fp.close()

    def test_release_swallows_unlock_error(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(sys, "platform", "win32")
        fake = _FakeMsvcrt(unlock_should_fail=True)
        monkeypatch.setitem(sys.modules, "msvcrt", fake)
        fp = open(tmp_path / "w.lock", "a+b")  # noqa: SIM115
        try:
            # Must not raise: a failed unlock is benign (kernel reclaims on close).
            _release(fp)
            assert fake.calls == [(fp.fileno(), _FakeMsvcrt.LK_UNLCK, 1)]
        finally:
            fp.close()


class TestPosixBranchInjected:
    """Cover the ``fcntl`` acquire/release branch on any host."""

    def test_acquire_success_takes_exclusive_lock(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(sys, "platform", "linux")
        fake = _FakeFcntl()
        monkeypatch.setitem(sys.modules, "fcntl", fake)
        fp = open(tmp_path / "p.lock", "a+b")  # noqa: SIM115
        try:
            _acquire(fp, timeout_s=1.0, poll_s=_POLL_S)
            assert fake.calls == [_FakeFcntl.LOCK_EX | _FakeFcntl.LOCK_NB]
        finally:
            fp.close()

    def test_acquire_times_out_and_retries(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(sys, "platform", "linux")
        fake = _FakeFcntl(lock_should_block=True)
        monkeypatch.setitem(sys.modules, "fcntl", fake)
        fp = open(tmp_path / "p.lock", "a+b")  # noqa: SIM115
        try:
            t0 = time.monotonic()
            with pytest.raises(TimeoutError, match="p.lock"):
                _acquire(fp, timeout_s=0.2, poll_s=_POLL_S)
            elapsed = time.monotonic() - t0
            assert elapsed >= 0.2 * 0.4
            assert elapsed < 0.2 * 20
            assert len(fake.calls) >= 2
            assert all(
                op == _FakeFcntl.LOCK_EX | _FakeFcntl.LOCK_NB for op in fake.calls
            )
        finally:
            fp.close()

    def test_release_unlocks(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(sys, "platform", "linux")
        fake = _FakeFcntl()
        monkeypatch.setitem(sys.modules, "fcntl", fake)
        fp = open(tmp_path / "p.lock", "a+b")  # noqa: SIM115
        try:
            _release(fp)
            assert fake.calls == [_FakeFcntl.LOCK_UN]
        finally:
            fp.close()

    def test_release_swallows_unlock_error(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(sys, "platform", "linux")
        fake = _FakeFcntl(unlock_should_fail=True)
        monkeypatch.setitem(sys.modules, "fcntl", fake)
        fp = open(tmp_path / "p.lock", "a+b")  # noqa: SIM115
        try:
            # The outer ``except Exception`` must swallow the unlock error.
            _release(fp)
            assert fake.calls == [_FakeFcntl.LOCK_UN]
        finally:
            fp.close()
