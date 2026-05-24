"""Hardware and runtime snapshot for reproducible benchmark output.

Every benchmark run drops a ``hardware.json`` next to its result so
readers can interpret the numbers in context. The probe is deliberately
shallow - just the fields a reader needs to decide whether two runs are
comparable.

Why not ``platform.uname()`` for everything: the fields below are the
ones that materially affect numbers in the v1.0 benchmark set. CPU
model and core count drive latency; OS family changes file-IO
behavior; Python and (when available) Node versions pin the runtime.
"""

from __future__ import annotations

import json
import os
import platform
import shutil
import subprocess
import sys
from dataclasses import asdict, dataclass
from pathlib import Path


@dataclass
class HardwareSnapshot:
    """Captured at the start of every benchmark run."""

    os: str
    os_release: str
    cpu_model: str
    cpu_count_logical: int
    python_version: str
    node_version: str | None
    mneme_bench_seed: int


def _detect_cpu_model() -> str:
    """Best-effort CPU model string across OSes.

    Linux: ``/proc/cpuinfo`` first non-empty ``model name``.
    macOS: ``sysctl -n machdep.cpu.brand_string`` when ``sysctl`` is on
    PATH. Windows and fallback: ``platform.processor()``.
    """
    system = platform.system().lower()
    if system == "linux":
        try:
            cpuinfo = Path("/proc/cpuinfo").read_text(encoding="utf-8")
            for line in cpuinfo.splitlines():
                if line.startswith("model name"):
                    return line.split(":", 1)[1].strip()
        except OSError:
            pass
    if system == "darwin" and shutil.which("sysctl") is not None:
        try:
            out = subprocess.run(  # noqa: S603,S607
                ["sysctl", "-n", "machdep.cpu.brand_string"],
                capture_output=True,
                text=True,
                check=False,
                timeout=2,
            )
            if out.returncode == 0:
                return out.stdout.strip()
        except (OSError, subprocess.SubprocessError):
            pass
    return platform.processor() or "unknown"


def _detect_node_version() -> str | None:
    """Return the ``node --version`` output, or ``None`` when absent."""
    if shutil.which("node") is None:
        return None
    try:
        out = subprocess.run(  # noqa: S603,S607
            ["node", "--version"],
            capture_output=True,
            text=True,
            check=False,
            timeout=2,
        )
        if out.returncode == 0:
            return out.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return None
    return None


def capture_hardware(seed: int = 42) -> HardwareSnapshot:
    """Capture the snapshot. Always returns; never raises."""
    cpu_count = os.cpu_count() or 0
    return HardwareSnapshot(
        os=platform.system() or "unknown",
        os_release=platform.release() or "unknown",
        cpu_model=_detect_cpu_model(),
        cpu_count_logical=cpu_count,
        python_version=sys.version.split()[0],
        node_version=_detect_node_version(),
        mneme_bench_seed=seed,
    )


def write_hardware_json(snapshot: HardwareSnapshot, out_path: Path) -> None:
    """Serialize the snapshot to ``out_path`` as pretty JSON."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps(asdict(snapshot), indent=2) + "\n",
        encoding="utf-8",
    )
