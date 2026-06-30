"""Repository release-integrity checks for public GitHub state."""

from __future__ import annotations

import json
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
    if "9 MCP tools" not in readme:
        errors.append("README must describe lite as nine MCP tools")
    if "mneme upgrade --profile=standard" not in readme:
        errors.append("README must document the supported upgrade command")

    mcp_pkg = json.loads(_read("packages/mneme-mcp/package.json"))
    if mcp_pkg.get("mcpName") != "io.github.TheGoatPsy/mneme":
        errors.append(
            "mneme-mcp package.json must declare the MCP Registry mcpName "
            "(a tree snapshot once dropped it; npm metadata is immutable)"
        )
    if "9 tools" not in mcp_pkg.get("description", ""):
        errors.append("mneme-mcp package.json description must say 9 tools")
    server_manifest = json.loads(_read("server.json"))
    if server_manifest.get("name") != mcp_pkg.get("mcpName"):
        errors.append(
            "server.json name must match package.json mcpName exactly "
            "(the MCP Registry compares casing byte for byte)"
        )
    if "## [1.0.1]" not in changelog or "## [1.0.0]" not in changelog:
        errors.append("CHANGELOG must contain separate 1.0.1 and 1.0.0 sections")
    if not (REPO_ROOT / "docs/RELEASE.md").is_file():
        errors.append("docs/RELEASE.md release checklist is missing")
    upgrading_path = REPO_ROOT / "docs/UPGRADING.md"
    if not upgrading_path.is_file():
        errors.append("docs/UPGRADING.md upgrade guide is missing")
    else:
        upgrading = upgrading_path.read_text(encoding="utf-8")
        for needle in ("Apache-2.0", "Node", "summary.json"):
            if needle not in upgrading:
                errors.append(f"docs/UPGRADING.md must cover {needle}")

    tracked_markdown = [
        path
        for path in _tracked_files()
        if path.suffix.lower() == ".md" and path.name != "CHANGELOG.md"
    ]
    for path in tracked_markdown:
        text = path.read_text(encoding="utf-8")
        if "--upgrade-profile" in text:
            errors.append(f"stale --upgrade-profile docs in {path.relative_to(REPO_ROOT)}")
        if "six tools" in text.lower():
            errors.append(f"stale six-tools claim in {path.relative_to(REPO_ROOT)}")

    codes = _error_codes_from_ts()
    for code in sorted(codes):
        if f"`{code}`" not in mcp_docs and f'"{code}' not in mcp_docs:
            errors.append(f"docs/MCP.md does not document {code}")
    for stale_code in ("INDEX_NOT_BUILT", "PROFILE_MISMATCH", "PATH_TRAVERSAL", "INTERNAL"):
        if stale_code in mcp_docs:
            errors.append(f"docs/MCP.md still contains stale error code {stale_code}")

    if not (REPO_ROOT / "packages/mneme-core/src/mneme_core/__main__.py").is_file():
        errors.append("mneme_core module execution entry point is missing")

    license_text = _read("LICENSE")
    if "Apache License" not in license_text or "Version 2.0, January 2004" not in license_text:
        errors.append("LICENSE must contain the verbatim Apache License 2.0 text")
    if not (REPO_ROOT / "NOTICE").is_file():
        errors.append("NOTICE file is missing (required by the Apache-2.0 convention)")
    for manifest in (
        "package.json",
        "packages/mneme-mcp/package.json",
    ):
        if '"license": "Apache-2.0"' not in _read(manifest):
            errors.append(f"{manifest} license field must be Apache-2.0")
    for manifest in (
        "packages/mneme-core/pyproject.toml",
        "packages/mneme-cc-plugin/pyproject.toml",
        "packages/mneme-graph/pyproject.toml",
        "packages/mneme-code/pyproject.toml",
    ):
        text = _read(manifest)
        if 'license = { text = "Apache-2.0" }' not in text:
            errors.append(f"{manifest} license field must be Apache-2.0")
        if "License :: OSI Approved :: Apache Software License" not in text:
            errors.append(f"{manifest} must carry the Apache classifier")
    if "license: Apache-2.0" not in _read("CITATION.cff"):
        errors.append("CITATION.cff license must be Apache-2.0")

    if errors:
        print("Repository integrity check failed:")
        for error in errors:
            print(f"- {error}")
        return 1
    print("Repository integrity check passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
