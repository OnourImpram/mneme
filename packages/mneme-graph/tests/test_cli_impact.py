"""CLI impact subcommand: file-seeded reverse-BFS over a built graph.

Includes the self-verification case: mneme-graph builds a graph over
its own package source and the impact of changing ``analytics.py``
must reach ``cli.py`` (which imports it).
"""

from __future__ import annotations

import shutil
from pathlib import Path

from mneme_graph.cli import run_build, run_impact

PACKAGE_DIR = Path(__file__).resolve().parents[1] / "mneme_graph"


def _make_fixture_vault(tmp_path: Path) -> Path:
    (tmp_path / "src").mkdir(parents=True)
    (tmp_path / "src" / "alpha.py").write_text(
        "def base() -> int:\n    return 1\n", encoding="utf-8"
    )
    (tmp_path / "src" / "beta.py").write_text(
        "from src.alpha import base\n\n\ndef caller() -> int:\n    return base()\n",
        encoding="utf-8",
    )
    return tmp_path


class TestRunImpact:
    def test_change_propagates_upstream(self, tmp_path: Path) -> None:
        vault = _make_fixture_vault(tmp_path)
        run_build(vault)
        impact = run_impact(vault, ["src/alpha.py"])
        assert impact["changed_files"] == ["src/alpha.py"]
        assert int(str(impact["seed_nodes"])) >= 1
        affected_files = impact["affected_files"]
        assert isinstance(affected_files, list)
        assert "src/beta.py" in affected_files

    def test_unmatched_file_reported(self, tmp_path: Path) -> None:
        vault = _make_fixture_vault(tmp_path)
        run_build(vault)
        impact = run_impact(vault, ["src/ghost.py"])
        assert impact["unmatched_files"] == ["src/ghost.py"]
        assert impact["seed_nodes"] == 0
        assert impact["affected_nodes"] == []

    def test_backslash_paths_normalized(self, tmp_path: Path) -> None:
        vault = _make_fixture_vault(tmp_path)
        run_build(vault)
        impact = run_impact(vault, ["src\\alpha.py"])
        assert impact["changed_files"] == ["src/alpha.py"]

    def test_max_depth_limits_traversal(self, tmp_path: Path) -> None:
        vault = _make_fixture_vault(tmp_path)
        run_build(vault)
        unbounded = run_impact(vault, ["src/alpha.py"])
        depth_zero = run_impact(vault, ["src/alpha.py"], max_depth=0)
        assert len(list(depth_zero["affected_nodes"])) <= len(
            list(unbounded["affected_nodes"])
        )
        assert depth_zero["affected_nodes"] == []


class TestSelfVerification:
    def test_graph_builds_over_own_source_and_impact_reaches_cli(
        self, tmp_path: Path
    ) -> None:
        """Dogfood: the package analyses itself (derived, rebuildable)."""
        target = tmp_path / "mneme_graph"
        shutil.copytree(
            PACKAGE_DIR, target, ignore=shutil.ignore_patterns("__pycache__")
        )
        summary = run_build(tmp_path)
        assert summary["nodes"] > 20
        assert summary["edges"] > 10
        impact = run_impact(tmp_path, ["mneme_graph/analytics.py"])
        affected_files = impact["affected_files"]
        assert isinstance(affected_files, list)
        assert "mneme_graph/cli.py" in affected_files
