"""Test fixtures shared across parity tests."""

from __future__ import annotations

import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_REPO_ROOT))
sys.path.insert(0, str(_REPO_ROOT / "benchmarks" / "head-to-head"))
sys.path.insert(0, str(_REPO_ROOT / "packages" / "mneme-core" / "src"))
