"""The version-lockstep gate must fail on an unreadable source.

WHY THIS EXISTS
``check_consistency`` dropped ERROR entries before comparing versions, and
``--check`` applied the same filter to the target comparison. Together that
made the release preflight pass on the exact condition it exists to catch:
delete or corrupt one of the eighteen declared version sources, and every
source that still parses agrees with itself, so the gate reports consensus
over a missing file. It is the gate that guards the tag, and the publish
workflow is fully automated off that tag.

A gate that passes proves nothing until it has been proven able to fail, so
this file asserts both directions: the real tree agrees, and a tree with one
source removed does not.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest


def _find_version_bump() -> Path | None:
    """Walk up for tools/version_bump.py rather than counting directories.

    A hardcoded ``parents[N]`` is how this file first landed: the count was off
    by one, the skipif fired, and all three tests reported as skipped — a check
    that cannot run, presented as no problem. That is the same defect class the
    tests below exist to close, so the lookup is made unable to rot instead.
    """
    for parent in Path(__file__).resolve().parents:
        candidate = parent / "tools" / "version_bump.py"
        if candidate.is_file():
            return candidate
    return None


_MODULE_PATH = _find_version_bump()


def _load_version_bump():
    """Import tools/version_bump.py, which lives outside any package."""
    assert _MODULE_PATH is not None
    spec = importlib.util.spec_from_file_location("_version_bump", _MODULE_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["_version_bump"] = module
    spec.loader.exec_module(module)
    return module


pytestmark = pytest.mark.skipif(
    _MODULE_PATH is None,
    reason="version_bump.py is absent (installed package, not a repo checkout)",
)


def test_the_real_tree_agrees() -> None:
    """Positive arm: without it, a gate stuck at False would pass the next test."""
    vb = _load_version_bump()
    agree, seen = vb.check_consistency()
    assert agree, [entry for entry in seen if entry[1].startswith("ERROR:")]
    assert len(seen) > 1, "a single source cannot demonstrate lockstep"


def test_an_unreadable_source_is_disagreement_not_an_exclusion(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The measured defect: one missing file used to leave consensus intact."""
    vb = _load_version_bump()
    missing = vb.SOURCES[0].__class__(
        label="deliberately absent",
        path=tmp_path / "does-not-exist.toml",
        flavor=vb.SOURCES[0].flavor,
    )
    monkeypatch.setattr(vb, "SOURCES", (*vb.SOURCES, missing))

    agree, seen = vb.check_consistency()
    assert not agree, "an unreadable version source must break consensus"
    assert any(
        label == "deliberately absent" and version.startswith("ERROR:")
        for label, version in seen
    ), seen


def test_an_unreadable_source_cannot_match_the_target(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """`--check <version>` must not treat 'could not read' as 'declares it'."""
    vb = _load_version_bump()
    _, seen = vb.check_consistency()
    target = seen[0][1]

    missing = vb.SOURCES[0].__class__(
        label="deliberately absent",
        path=tmp_path / "does-not-exist.toml",
        flavor=vb.SOURCES[0].flavor,
    )
    monkeypatch.setattr(vb, "SOURCES", (*vb.SOURCES, missing))
    monkeypatch.setattr(sys, "argv", ["version_bump.py", target, "--check"])

    assert vb.main() == 1, "--check must exit non-zero when a source is unreadable"
