"""A vault must be able to exclude material that is not its own content.

WHY THIS EXISTS
``DEFAULT_EXCLUDE_PATTERNS`` was a module constant with no per-vault override.
That is fine until a vault mirrors something large that is not its own writing.
Measured on a real 12,352-document vault: 57% of the index was third-party
material, and a single mirrored plugin home accounted for 56.5% of it — every
one of those documents competing with the operator's own notes for BM25 rank.
There was no way to keep them out short of editing the constant in the
installed package.

WHAT IS PINNED
Exclusions compose from three sources — the built-in list, the vault's own
``[index] exclude`` in ``.mneme/config.toml``, and repeatable ``--exclude``
flags — and they ADD, never substitute. A config that could replace the
defaults would let one typo put ``.git`` and ``node_modules`` back into the
index, so the test asserts the built-ins survive every configuration.

The negative controls carry the weight here. An exclusion feature that
excluded nothing would satisfy "the wanted file is present" on its own, and a
feature that excluded everything would satisfy "the unwanted file is absent".
Both halves are asserted on every path, and a malformed config is required to
fail loudly rather than quietly index everything.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from click.testing import CliRunner

from mneme_core.cli import cli
from mneme_core.fts5.indexer import DEFAULT_EXCLUDE_PATTERNS


def _vault(tmp_path: Path, config: str | None = None) -> Path:
    root = tmp_path / "vault"
    (root / ".mneme").mkdir(parents=True, exist_ok=True)
    (root / "notes").mkdir(exist_ok=True)
    (root / "mirror").mkdir(exist_ok=True)
    (root / "notes" / "kept.md").write_text("my own note\n", encoding="utf-8")
    (root / "mirror" / "vendor.md").write_text("someone else's note\n", encoding="utf-8")
    if config is not None:
        (root / ".mneme" / "config.toml").write_text(config, encoding="utf-8")
    return root


def _rebuild(root: Path, *extra: str) -> dict:
    args = ["index", "rebuild", "--vault", str(root), "--locale", "en", *extra]
    result = CliRunner().invoke(cli, args)
    assert result.exit_code == 0, result.output
    return json.loads(result.output)


def _paths(root: Path) -> set[str]:
    conn = sqlite3.connect(root / ".mneme" / "fts5.sqlite")
    try:
        return {row[0] for row in conn.execute("SELECT path FROM documents")}
    finally:
        conn.close()


def test_negative_control_without_exclusions_everything_is_indexed(
    tmp_path: Path,
) -> None:
    """Establishes that the fixture's unwanted file IS reachable by default.

    Without this, every assertion below could be satisfied by a walk that
    never saw the file in the first place.
    """
    root = _vault(tmp_path)
    _rebuild(root)
    paths = _paths(root)
    assert "notes/kept.md" in paths
    assert "mirror/vendor.md" in paths


def test_cli_exclude_flag_drops_the_directory(tmp_path: Path) -> None:
    root = _vault(tmp_path)
    stats = _rebuild(root, "--exclude", "/mirror/")
    paths = _paths(root)

    assert "mirror/vendor.md" not in paths
    # The other half: excluding must not become excluding everything.
    assert "notes/kept.md" in paths
    assert stats["stats"]["skipped_excluded"] >= 1


def test_vault_config_exclude_drops_the_directory(tmp_path: Path) -> None:
    """The durable form: the vault declares what is not its own content."""
    root = _vault(tmp_path, 'profile = "lite"\n\n[index]\nexclude = ["/mirror/"]\n')
    _rebuild(root)
    paths = _paths(root)

    assert "mirror/vendor.md" not in paths
    assert "notes/kept.md" in paths


def test_config_and_flag_compose(tmp_path: Path) -> None:
    root = _vault(tmp_path, '[index]\nexclude = ["/mirror/"]\n')
    (root / "drafts").mkdir()
    (root / "drafts" / "wip.md").write_text("draft\n", encoding="utf-8")

    _rebuild(root, "--exclude", "/drafts/")
    paths = _paths(root)

    assert "mirror/vendor.md" not in paths
    assert "drafts/wip.md" not in paths
    assert "notes/kept.md" in paths


def test_negative_control_config_cannot_replace_the_built_ins(
    tmp_path: Path,
) -> None:
    """A vault may add exclusions; it may not subtract them.

    If a config could replace the defaults, one typo would put `.git` and
    `node_modules` back into the index. Asserted by construction on the
    resolver, and end-to-end on a real `.git` directory.
    """
    from mneme_core.cli import _resolve_exclude_patterns

    root = _vault(tmp_path, '[index]\nexclude = ["/mirror/"]\n')
    resolved = _resolve_exclude_patterns(root, ("/drafts/",))
    for builtin in DEFAULT_EXCLUDE_PATTERNS:
        assert builtin in resolved, f"{builtin} was dropped by a vault config"

    git_dir = root / ".git"
    git_dir.mkdir()
    (git_dir / "notes.md").write_text("internal\n", encoding="utf-8")
    _rebuild(root)
    assert ".git/notes.md" not in _paths(root)


def test_resolver_deduplicates_without_reordering(tmp_path: Path) -> None:
    """The reported list must mirror what actually ran, in order."""
    from mneme_core.cli import _resolve_exclude_patterns

    root = _vault(tmp_path, '[index]\nexclude = ["/mirror/", "/.git/"]\n')
    resolved = _resolve_exclude_patterns(root, ("/mirror/",))

    assert len(resolved) == len(set(resolved)), "duplicate exclusion patterns"
    assert resolved.index("/.git/") < resolved.index("/mirror/")


def test_a_malformed_config_fails_loudly(tmp_path: Path) -> None:
    """A swallowed exclusion list looks exactly like one that did nothing."""
    root = _vault(tmp_path, '[index]\nexclude = "not-a-list"\n')
    result = CliRunner().invoke(
        cli, ["index", "rebuild", "--vault", str(root), "--locale", "en"]
    )
    assert result.exit_code != 0
    assert "must be a list of strings" in result.output


def test_unparseable_toml_fails_loudly(tmp_path: Path) -> None:
    root = _vault(tmp_path, "[index\nexclude = [\n")
    result = CliRunner().invoke(
        cli, ["index", "rebuild", "--vault", str(root), "--locale", "en"]
    )
    assert result.exit_code != 0
    assert "could not be read" in result.output


def test_a_vault_with_no_config_still_works(tmp_path: Path) -> None:
    """The feature must be invisible to vaults that never asked for it."""
    from mneme_core.cli import _resolve_exclude_patterns

    root = _vault(tmp_path)
    assert _resolve_exclude_patterns(root) == DEFAULT_EXCLUDE_PATTERNS
