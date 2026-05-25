"""Regression tests for the cost-ledger lost-write race.

Before the fix, ``append_cost`` did an *unlocked* ``open("a")`` append while
``settle_reservation`` / ``rollback_reservation`` held the sidecar lock across
a read-then-rewrite. An append that landed between settle's read snapshot and
its ``os.replace`` was destroyed by the rewrite: a committed charge vanished
and ``month_to_date_spend`` under-counted, the unsafe direction for a cost cap.

Two tests pin the fix:

* :func:`test_no_committed_record_lost_under_concurrency` — many OS processes
  interleave ``append_cost`` with reserve/settle cycles against one ledger
  under a tight cap, then assert the cap is never exceeded and not one
  committed record was lost.
* :func:`test_injected_append_survives_concurrent_settle` — a deterministic
  reproduction of the exact interleaving: a settle is paused mid-rewrite (after
  it takes its read snapshot) while a second thread appends ``$0.50``; the
  injected charge must survive.

Separate OS processes (not threads) are used for the concurrency test because
``fcntl.flock`` / ``msvcrt.locking`` semantics only serialize across
independent open file descriptions, which threads sharing one descriptor would
not exercise. ``subprocess`` is used rather than the ``multiprocessing`` module
because a pytest-module-level worker cannot be re-imported by a Windows
``spawn`` child to reconstruct it; spawning ``sys.executable -c`` sidesteps
that entirely and runs identically on Windows and Linux CI.
"""

from __future__ import annotations

import json
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from mneme_core.compression.ledger import (
    append_cost,
    month_to_date_spend,
    reserve_cost,
    settle_reservation,
)

# Embedded worker run as ``python -c``. Each process interleaves a direct
# append (kind "direct", outside the compression cap) with one reserve/settle
# cycle (kind "compression", cap-gated), and reports the cost it actually
# committed so the parent can assert exact ledger integrity. Results are
# exchanged as JSON over stdout; no binary object serialization is involved.
_WORKER_SRC = r"""
import json
import sys
from pathlib import Path

from mneme_core.compression.ledger import (
    append_cost,
    reserve_cost,
    settle_reservation,
)


def main() -> None:
    ledger = Path(sys.argv[1])
    n_iters = int(sys.argv[2])
    cap = float(sys.argv[3])
    append_each = float(sys.argv[4])
    reserve_est = float(sys.argv[5])
    tag = sys.argv[6]
    appended_total = 0.0
    settled_total = 0.0
    for _ in range(n_iters):
        append_cost(
            ledger,
            model="m",
            tokens_in=1,
            tokens_out=1,
            cost_usd=append_each,
            host=tag,
            kind="direct",
        )
        appended_total += append_each
        rid, ok = reserve_cost(
            ledger,
            estimated_cost_usd=reserve_est,
            host=tag,
            cap_usd_monthly=cap,
        )
        if ok:
            settle_reservation(
                ledger,
                rid,
                actual_cost_usd=reserve_est,
                model="m",
                tokens_in=1,
                tokens_out=1,
                host=tag,
            )
            settled_total += reserve_est
    print(json.dumps({"appended": appended_total, "settled": settled_total}))


if __name__ == "__main__":
    main()
"""


def test_no_committed_record_lost_under_concurrency(tmp_path: Path) -> None:
    ledger = tmp_path / "cost.jsonl"
    n_procs = 4
    iters = 15
    cap = 0.50
    append_each = 0.01
    reserve_est = 0.02

    procs: list[subprocess.Popen[str]] = []
    for i in range(n_procs):
        procs.append(
            subprocess.Popen(
                [
                    sys.executable,
                    "-c",
                    _WORKER_SRC,
                    str(ledger),
                    str(iters),
                    str(cap),
                    str(append_each),
                    str(reserve_est),
                    f"w{i}",
                ],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
        )

    appended_total = 0.0
    settled_total = 0.0
    for p in procs:
        out, err = p.communicate(timeout=120)
        assert p.returncode == 0, f"worker exited {p.returncode}: {err}"
        data = json.loads(out.strip().splitlines()[-1])
        appended_total += float(data["appended"])
        settled_total += float(data["settled"])

    # (a) The reservation path never lets compression spend exceed the cap.
    comp_spend = month_to_date_spend(ledger, kind="compression")
    assert comp_spend <= cap + 1e-9
    assert comp_spend == pytest.approx(settled_total, abs=1e-9)

    # No unlocked append was clobbered: every direct charge is still present.
    assert month_to_date_spend(ledger, kind="direct") == pytest.approx(
        appended_total, abs=1e-9
    )

    # (b) Total integrity: month_to_date_spend equals exactly the sum of every
    # successfully-appended and settled cost.
    assert month_to_date_spend(ledger, kind=None) == pytest.approx(
        appended_total + settled_total, abs=1e-9
    )


def test_injected_append_survives_concurrent_settle(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ledger = tmp_path / "cost.jsonl"
    # Seed a reservation that the settle below will drop and replace.
    reservation_id, ok = reserve_cost(
        ledger, estimated_cost_usd=0.10, host="h", cap_usd_monthly=100.0
    )
    assert ok

    import mneme_core.compression.ledger as ledger_mod

    real_read = ledger_mod._read_lines_dropping_reservation
    read_snapshot_taken = threading.Event()

    def patched_read(
        ledger_path: Path, *, reservation_id: str, target_kind: str
    ) -> list[str]:
        # Runs inside settle's lock, holding the read snapshot. Release the
        # appender to attempt its write now and widen the window the OLD
        # unlocked append was clobbered in. With the fix the appender blocks
        # on the same lock until this settle finishes and releases.
        kept = real_read(
            ledger_path, reservation_id=reservation_id, target_kind=target_kind
        )
        read_snapshot_taken.set()
        time.sleep(0.25)
        return kept

    monkeypatch.setattr(
        ledger_mod, "_read_lines_dropping_reservation", patched_read
    )

    def appender() -> None:
        assert read_snapshot_taken.wait(timeout=5.0)
        append_cost(
            ledger,
            model="m",
            tokens_in=1,
            tokens_out=1,
            cost_usd=0.50,
            host="h",
            kind="direct",
        )

    worker = threading.Thread(target=appender)
    worker.start()
    settle_reservation(
        ledger,
        reservation_id,
        actual_cost_usd=0.10,
        model="m",
        tokens_in=1,
        tokens_out=1,
        host="h",
    )
    worker.join(timeout=10.0)
    assert not worker.is_alive(), "appender thread did not finish (deadlock?)"

    # The injected $0.50 (direct) must survive the settle rewrite, alongside
    # the settled $0.10 (compression). Pre-fix, settle's os.replace clobbered
    # the unlocked append and the total would be only $0.10.
    assert month_to_date_spend(ledger, kind=None) == pytest.approx(
        0.60, abs=1e-9
    )
    assert month_to_date_spend(ledger, kind="compression") == pytest.approx(
        0.10, abs=1e-9
    )
