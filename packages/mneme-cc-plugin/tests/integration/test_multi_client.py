"""Regression test: concurrent Stop hook writers must not lose updates.

Two Claude Code clients (or Claude Code + Codex) both fire their Stop
hook at the same moment.  Each call does a read-modify-write of the
daily session log under a sidecar file lock.  This test proves that N
threads, all hammering ``_append_session_section`` simultaneously, each
get their ``## HH:MM session sess-{i}`` block persisted — no lost
update, no torn file.

If the lock were absent (or broken) at least one writer would silently
overwrite another writer's block and some ``sess-{i}`` id would be
missing from the file.  The assertion at the bottom catches that class
of regression.
"""

from __future__ import annotations

import threading
from datetime import date
from pathlib import Path

from mneme_core.vault import frontmatter
from mneme_core.vault.config import VaultConfig

from mneme_cc_plugin.hooks import stop

N_WRITERS = 8


def test_concurrent_writers_no_lost_update(tmp_path: Path) -> None:
    """All N concurrent _append_session_section calls survive intact."""
    vault = VaultConfig.from_path(tmp_path)

    # Use a Barrier so all threads start their write at the same moment,
    # maximising lock contention and exposing any lost-update race.
    barrier = threading.Barrier(N_WRITERS)
    errors: list[Exception] = []

    def writer(i: int) -> None:
        barrier.wait()  # synchronise all threads at the starting line
        try:
            stop._append_session_section(
                vault,
                session_id=f"sess-{i}",
                transcript_path="",
            )
        except Exception as exc:
            errors.append(exc)

    threads = [threading.Thread(target=writer, args=(i,)) for i in range(N_WRITERS)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    # Propagate any unexpected exceptions from worker threads.
    assert not errors, f"Worker threads raised exceptions: {errors}"

    # The daily log must exist.
    today_iso = date.today().isoformat()
    session_file: Path = vault.root / stop.LOG_DIR_NAME / f"{today_iso}.md"
    assert session_file.exists(), "Daily session log was never created"

    content = session_file.read_text(encoding="utf-8")

    # Every session id must appear exactly once — no lost update.
    for i in range(N_WRITERS):
        assert f"sess-{i}" in content, (
            f"Session block for sess-{i} is missing from the daily log "
            f"(lost-update regression). File contents:\n{content}"
        )

    # The file must still carry valid ``type: session`` frontmatter so
    # the FTS5 indexer and SessionStart continue to work correctly.
    fm, _body = frontmatter.parse(content)
    assert fm is not None, (
        "Daily session log lost its frontmatter block under concurrent writes"
    )
    assert fm.type == "session", (
        f"Expected frontmatter type 'session', got '{fm.type}'"
    )
