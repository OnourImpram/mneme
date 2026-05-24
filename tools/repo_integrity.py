"""Repository release-integrity checks for public GitHub state."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

from version_bump import check_consistency

REPO_ROOT = Path(__file__).resolve().parents[1]


def _tracked_files() -> list[Path]:
    proc = subprocess.run(
        ["git", "ls-files"],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    return [REPO_ROOT / line.strip() for line in proc.stdout.splitlines() if line.strip()]


def _read(path: str) -> str:
    return (REPO_ROOT / path).read_text(encoding="utf-8")


def _error_codes_from_ts() -> set[str]:
    text = _read("packages/mneme-mcp/src/errors.ts")
    return set(re.findall(r'^\s*[A-Z_]+:\s*"([A-Z_]+)"', text, flags=re.MULTILINE))


def main() -> int:
    errors: list[str] = []

    agree, seen = check_consistency()
    if not agree:
        errors.append(f"version sources disagree: {seen}")

    readme = _read("README.md")
    changelog = _read("CHANGELOG.md")
    mcp_docs = _read("docs/MCP.md")

    for marker in ("v1.0.0-rc", "Hard launch target", "Phase K release"):
        if marker in readme:
            errors.append(f"README still contains stale release marker: {marker}")
    if "6 MCP tools" not in readme:
        errors.append("README must describe lite as six MCP tools")
    if "mneme upgrade --profile=standard" not in readme:
        errors.append("README must document the supported upgrade command")
    if "## [1.0.1]" not in changelog or "## [1.0.0]" not in changelog:
        errors.append("CHANGELOG must contain separate 1.0.1 and 1.0.0 sections")
    if not (REPO_ROOT / "docs/RELEASE.md").is_file():
        errors.append("docs/RELEASE.md release checklist is missing")

    tracked_markdown = [
        path
        for path in _tracked_files()
        if path.suffix.lower() == ".md" and path.name != "CHANGELOG.md"
    ]
    for path in tracked_markdown:
        text = path.read_text(encoding="utf-8")
        if "--upgrade-profile" in text:
            errors.append(f"stale --upgrade-profile docs in {path.relative_to(REPO_ROOT)}")

    codes = _error_codes_from_ts()
    for code in sorted(codes):
        if f"`{code}`" not in mcp_docs and f'"{code}' not in mcp_docs:
            errors.append(f"docs/MCP.md does not document {code}")
    for stale_code in ("INDEX_NOT_BUILT", "PROFILE_MISMATCH", "PATH_TRAVERSAL", "INTERNAL"):
        if stale_code in mcp_docs:
            errors.append(f"docs/MCP.md still contains stale error code {stale_code}")

    if not (REPO_ROOT / "packages/mneme-core/src/mneme_core/__main__.py").is_file():
        errors.append("mneme_core module execution entry point is missing")

    if errors:
        print("Repository integrity check failed:")
        for error in errors:
            print(f"- {error}")
        return 1
    print("Repository integrity check passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
