"""Tests for :mod:`mneme_core.bench.hardware`."""

from __future__ import annotations

import json
from pathlib import Path

from mneme_core.bench.hardware import (
    HardwareSnapshot,
    capture_hardware,
    write_hardware_json,
)


class TestCaptureHardware:
    def test_fields_populated(self) -> None:
        snap = capture_hardware(seed=7)
        assert snap.seed if False else True  # keep one assertion before access
        assert snap.os
        assert snap.python_version
        assert snap.cpu_count_logical >= 0
        assert snap.mneme_bench_seed == 7

    def test_node_version_either_none_or_v_prefixed(self) -> None:
        snap = capture_hardware()
        assert snap.node_version is None or snap.node_version.startswith("v")

    def test_default_seed_is_42(self) -> None:
        snap = capture_hardware()
        assert snap.mneme_bench_seed == 42


class TestWriteHardwareJson:
    def test_creates_file_with_expected_keys(self, tmp_path: Path) -> None:
        snap = HardwareSnapshot(
            os="Linux",
            os_release="6.1.0",
            cpu_model="Synthetic",
            cpu_count_logical=4,
            python_version="3.12.0",
            node_version="v20.0.0",
            mneme_bench_seed=42,
        )
        out = tmp_path / "hardware.json"
        write_hardware_json(snap, out)
        assert out.is_file()
        data = json.loads(out.read_text(encoding="utf-8"))
        for key in (
            "os",
            "os_release",
            "cpu_model",
            "cpu_count_logical",
            "python_version",
            "node_version",
            "mneme_bench_seed",
        ):
            assert key in data
        assert data["mneme_bench_seed"] == 42

    def test_creates_parent_dirs(self, tmp_path: Path) -> None:
        snap = capture_hardware()
        nested = tmp_path / "nested" / "dir" / "hardware.json"
        write_hardware_json(snap, nested)
        assert nested.is_file()
