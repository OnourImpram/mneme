"""Guard ADR-014 skill-tree sync between the cc-plugin and codex-plugin.

ADR-014 ships a native plugin PER CLIENT over one shared core, so the two skill
trees are deliberately NOT byte-identical: invocation prose legitimately differs
(Claude Code uses ``/mneme:prime``; Codex uses ``/skills`` or ``$mneme-prime``),
and the Codex copies name the MCP server explicitly. What must stay in sync is
the capability surface, not the wording. This test fails on the drift that
actually violates ADR-014:

* a skill present in one plugin but missing from the other (set drift);
* a skill whose frontmatter ``name`` differs across plugins (identity drift);
* a skill whose ordered section headings differ across plugins (capability
  drift).

It intentionally does not assert byte-identical bodies. Forcing that would
corrupt the correct client-specific invocation docs that ADR-014 requires.
"""

from __future__ import annotations

import re
from pathlib import Path

_HEADING_RE = re.compile(r"^#{1,6} .+$", re.MULTILINE)


def _roots() -> tuple[Path, Path]:
    repo_root = Path(__file__).resolve().parents[4]
    return (
        repo_root / "packages" / "mneme-cc-plugin" / "skills",
        repo_root / "packages" / "mneme-codex-plugin" / "skills",
    )


def _skill_dirs(skills_root: Path) -> dict[str, Path]:
    return {
        child.name: child
        for child in skills_root.iterdir()
        if child.is_dir() and (child / "SKILL.md").is_file()
    }


def _frontmatter_name(skill_md: Path) -> str | None:
    text = skill_md.read_text(encoding="utf-8")
    if not text.startswith("---\n"):
        return None
    frontmatter = text.split("---", 2)[1]
    for line in frontmatter.splitlines():
        if line.startswith("name:"):
            return line[len("name:") :].strip()
    return None


def _headings(skill_md: Path) -> list[str]:
    text = skill_md.read_text(encoding="utf-8")
    body = text.split("---", 2)[2] if text.startswith("---\n") else text
    return _HEADING_RE.findall(body)


def test_skill_sets_match() -> None:
    cc_root, codex_root = _roots()
    cc = set(_skill_dirs(cc_root))
    codex = set(_skill_dirs(codex_root))
    assert cc == codex, (
        "Skill-set drift (ADR-014): "
        f"only in cc={sorted(cc - codex)}, only in codex={sorted(codex - cc)}"
    )


def test_skill_identity_and_structure_match() -> None:
    cc_root, codex_root = _roots()
    cc_dirs = _skill_dirs(cc_root)
    codex_dirs = _skill_dirs(codex_root)
    problems: list[str] = []
    for name in sorted(set(cc_dirs) & set(codex_dirs)):
        cc_md = cc_dirs[name] / "SKILL.md"
        codex_md = codex_dirs[name] / "SKILL.md"
        cc_name = _frontmatter_name(cc_md)
        codex_name = _frontmatter_name(codex_md)
        if cc_name != codex_name:
            problems.append(
                f"{name}: frontmatter name drift cc={cc_name!r} codex={codex_name!r}"
            )
        cc_headings = _headings(cc_md)
        codex_headings = _headings(codex_md)
        if cc_headings != codex_headings:
            problems.append(
                f"{name}: section-structure drift "
                f"cc={cc_headings} codex={codex_headings}"
            )
    assert not problems, "ADR-014 skill drift:\n" + "\n".join(problems)
