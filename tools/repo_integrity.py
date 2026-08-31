"""Repository release-integrity checks for public GitHub state."""

from __future__ import annotations

import json
import re
import subprocess
import tomllib
from pathlib import Path
from typing import Any

from version_bump import SOURCES, check_consistency

REPO_ROOT = Path(__file__).resolve().parents[1]

EXPECTED_VERSION_SOURCE_LABELS = (
    "mneme-core pyproject",
    "mneme-mcp package.json",
    "mneme-cc-plugin pyproject",
    "mneme-graph pyproject",
    "mneme-code pyproject",
    "mneme-cc-plugin plugin.json",
    "mneme-cc-plugin native .claude-plugin manifest",
    "mneme-codex-plugin manifest",
    "Claude marketplace entry",
    "root package.json",
    "mneme-core __version__",
    "mneme-cc-plugin __version__",
    "mneme-mcp SERVER_VERSION",
    "CITATION.cff",
    "mneme-antigravity-plugin manifest",
    "MCP Registry server.json (top-level)",
    "MCP Registry server.json (package entry)",
    "README status line",
)
EXPECTED_TOOL_NAMES = (
    "mneme_search",
    "mneme_recall",
    "mneme_write",
    "mneme_summarize",
    "mneme_timeline",
    "mneme_prime",
    "mneme_propose",
    "mneme_health",
    "mneme_checkpoint_list",
    "mneme_working_set_load",
)
CLIENT_MANIFESTS = (
    "packages/mneme-cc-plugin/.claude-plugin/plugin.json",
    "packages/mneme-codex-plugin/.codex-plugin/plugin.json",
    "packages/mneme-antigravity-plugin/gemini-extension.json",
)
STABLE_WORKFLOWS = {
    "bench.yml",
    "ci.yml",
    "codeql.yml",
    "publish-npm.yml",
    "release.yml",
}
FORBIDDEN_RELEASE_WORKFLOWS = {
    "mneme-3.6.0-orchestrator.yml",
    "one-time-publish-mneme-3.6.0.yml",
    "prepare-release-3.6.0.yml",
    "release-on-version-merge.yml",
}
EXPECTED_COVERAGE_FILES = {
    "packages/mneme-core/pyproject.toml": ".coverage.mneme-core",
    "packages/mneme-cc-plugin/pyproject.toml": ".coverage.mneme-cc-plugin",
    "packages/mneme-graph/pyproject.toml": ".coverage.mneme-graph",
    "packages/mneme-code/pyproject.toml": ".coverage.mneme-code",
}
IMMUTABLE_MCP_NAME = "io.github.OnourImpram/mneme"
IMMUTABLE_MCP_NAME_LOCATIONS = {
    "packages/mneme-mcp/package.json": 1,
    "server.json": 1,
    "tools/repo_integrity.py": 1,
}


def _candidate_files(repo_root: Path = REPO_ROOT) -> list[Path]:
    proc = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
        cwd=repo_root,
        check=True,
        capture_output=True,
        stdin=subprocess.DEVNULL,
    )
    paths = []
    for raw_path in proc.stdout.split(b"\0"):
        if not raw_path:
            continue
        path = repo_root / raw_path.decode("utf-8")
        if path.is_file():
            paths.append(path)
    return paths


def _read(path: str, repo_root: Path = REPO_ROOT) -> str:
    return (repo_root / path).read_text(encoding="utf-8")


def _read_json(path: str, repo_root: Path = REPO_ROOT) -> dict[str, Any]:
    payload = json.loads(_read(path, repo_root))
    if not isinstance(payload, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return payload


def _error_codes_from_ts(repo_root: Path = REPO_ROOT) -> set[str]:
    text = _read("packages/mneme-mcp/src/errors.ts", repo_root)
    return set(re.findall(r'^\s*[A-Z_]+:\s*"([A-Z_]+)"', text, flags=re.MULTILINE))


def _tool_names_from_registry(text: str) -> tuple[str, ...]:
    return tuple(
        re.findall(
            r'^\s*name:\s*"(mneme_[a-z0-9_]+)",\s*$',
            text,
            flags=re.MULTILINE,
        )
    )


def _immutable_name_locations(repo_root: Path = REPO_ROOT) -> dict[str, int]:
    needle = IMMUTABLE_MCP_NAME.encode("utf-8")
    locations: dict[str, int] = {}
    for path in _candidate_files(repo_root):
        count = path.read_bytes().count(needle)
        if count:
            locations[path.relative_to(repo_root).as_posix()] = count
    return locations


def _check_release_workflows(errors: list[str], repo_root: Path) -> None:
    workflow_root = repo_root / ".github" / "workflows"
    actual = {path.name for path in workflow_root.glob("*.yml") if path.is_file()}
    missing = STABLE_WORKFLOWS - actual
    unexpected_release = FORBIDDEN_RELEASE_WORKFLOWS & actual
    if missing:
        errors.append(f"stable workflows are missing: {sorted(missing)}")
    if unexpected_release:
        errors.append(
            "temporary release workflows must not be present: "
            f"{sorted(unexpected_release)}"
        )
    ready_markers = sorted(path.name for path in (repo_root / ".github").glob("*.ready"))
    if ready_markers:
        errors.append(f"temporary release ready markers must not be present: {ready_markers}")

    ci_text = _read(".github/workflows/ci.yml", repo_root)
    actionlint_requirements = (
        "ACTIONLINT_VERSION: 1.7.7",
        "023070a287cd8cccd71515fedc843f1985bf96c436b7effaecce67290e7e0757",
        "./actionlint -color",
    )
    for requirement in actionlint_requirements:
        if requirement not in ci_text:
            errors.append(f"ci.yml is missing pinned actionlint requirement: {requirement}")

    release_text = _read(".github/workflows/release.yml", repo_root)
    release_requirements = (
        "Manual dispatch cannot publish. Push a verified release tag instead.",
        "EVENT_NAME: ${{ github.event_name }}",
        "MCP_PUBLISHER_VERSION: v1.8.0",
        "1370446bbe74d562608e8005a6ccce02d146a661fbd78674e11cc70b9618d6cf",
        "(cd release/artifacts && sha256sum -c SHA256SUMS)",
        "tag_name: v${{ needs.preflight.outputs.target_version }}",
        "publish-mcp-registry, pack-plugin",
    )
    for requirement in release_requirements:
        if requirement not in release_text:
            errors.append(f"release.yml is missing publish invariant: {requirement}")
    publish_tag_gate = (
        "github.event_name == 'push' && github.ref_type == 'tag'"
    )
    if release_text.count(publish_tag_gate) != 7:
        errors.append(
            "all seven publish jobs must require a push tag event"
        )
    if '[[ "$EVENT_NAME" == "push" && "$EVENT_REF_TYPE" == "tag" ]]' not in release_text:
        errors.append("release preflight must distinguish tag pushes from manual tag dispatches")
    if "releases/latest/download/mcp-publisher" in release_text:
        errors.append("release.yml must not download a mutable MCP publisher")
    if "name: cc-plugin-tarball" in release_text:
        errors.append("Claude release bytes must come directly from preflight artifacts")


def _check_version_sources(errors: list[str], repo_root: Path) -> None:
    labels = tuple(source.label for source in SOURCES)
    if labels != EXPECTED_VERSION_SOURCE_LABELS:
        errors.append(
            "version source registry must contain the exact 18 release sources: "
            f"{labels}"
        )
    for source in SOURCES:
        if not source.path.is_file():
            errors.append(f"version source is missing: {source.label} ({source.path})")
    agree, seen = check_consistency()
    if not agree:
        errors.append(f"version sources disagree: {seen}")
    if repo_root != REPO_ROOT:
        errors.append("version checks must run against the repository containing this tool")


def _check_tool_registry(errors: list[str], repo_root: Path) -> None:
    registry = _read("packages/mneme-mcp/src/tool_registry.ts", repo_root)
    actual = _tool_names_from_registry(registry)
    if actual != EXPECTED_TOOL_NAMES:
        errors.append(
            "MCP tool registry must expose the canonical tools in order.\n"
            f"  expected: {EXPECTED_TOOL_NAMES}\n"
            f"  actual:   {actual}"
        )


def _check_client_manifests(errors: list[str], repo_root: Path) -> None:
    if len(CLIENT_MANIFESTS) != 3:
        errors.append("repository truth must define exactly three native client manifests")
    versions: set[str] = set()
    for path in CLIENT_MANIFESTS:
        try:
            manifest = _read_json(path, repo_root)
        except (OSError, json.JSONDecodeError, ValueError) as exc:
            errors.append(f"invalid client manifest {path}: {exc}")
            continue
        if manifest.get("name") != "mneme":
            errors.append(f"{path} name must be mneme")
        version = manifest.get("version")
        if not isinstance(version, str):
            errors.append(f"{path} version must be a string")
        else:
            versions.add(version)
        if manifest.get("license") != "Apache-2.0":
            errors.append(f"{path} license must be Apache-2.0")
    if len(versions) != 1:
        errors.append(f"three native client manifest versions disagree: {sorted(versions)}")


def _check_licenses_and_engines(errors: list[str], repo_root: Path) -> None:
    root_package = _read_json("package.json", repo_root)
    mcp_package = _read_json("packages/mneme-mcp/package.json", repo_root)
    legacy_plugin = _read_json("packages/mneme-cc-plugin/plugin.json", repo_root)
    marketplace = _read_json(".claude-plugin/marketplace.json", repo_root)

    json_licenses = {
        "package.json": root_package.get("license"),
        "packages/mneme-mcp/package.json": mcp_package.get("license"),
        "packages/mneme-cc-plugin/plugin.json": legacy_plugin.get("license"),
    }
    plugins = marketplace.get("plugins")
    json_licenses[".claude-plugin/marketplace.json"] = (
        plugins[0].get("license")
        if isinstance(plugins, list) and len(plugins) == 1 and isinstance(plugins[0], dict)
        else None
    )
    for path, license_name in json_licenses.items():
        if license_name != "Apache-2.0":
            errors.append(f"{path} license must be Apache-2.0")

    expected_node = {
        "package.json": root_package.get("engines", {}).get("node"),
        "packages/mneme-mcp/package.json": mcp_package.get("engines", {}).get("node"),
        "packages/mneme-cc-plugin/plugin.json": legacy_plugin.get("engines", {}).get("node"),
    }
    for path, node_range in expected_node.items():
        if node_range != ">=22":
            errors.append(f"{path} Node engine must be >=22")

    for path in EXPECTED_COVERAGE_FILES:
        payload = tomllib.loads(_read(path, repo_root))
        project = payload.get("project", {})
        license_value = project.get("license") if isinstance(project, dict) else None
        if license_value != {"text": "Apache-2.0"}:
            errors.append(f"{path} license field must be Apache-2.0")
        classifiers = project.get("classifiers", []) if isinstance(project, dict) else []
        if "License :: OSI Approved :: Apache Software License" not in classifiers:
            errors.append(f"{path} must carry the Apache classifier")

    if "license: Apache-2.0" not in _read("CITATION.cff", repo_root):
        errors.append("CITATION.cff license must be Apache-2.0")


def _check_coverage_truth(errors: list[str], repo_root: Path) -> None:
    seen_data_files: set[str] = set()
    for path, expected_data_file in EXPECTED_COVERAGE_FILES.items():
        payload = tomllib.loads(_read(path, repo_root))
        coverage = payload.get("tool", {}).get("coverage", {}).get("run", {})
        pytest_options = payload.get("tool", {}).get("pytest", {}).get("ini_options", {})
        if coverage.get("branch") is not True:
            errors.append(f"{path} must enable branch coverage")
        data_file = coverage.get("data_file")
        if data_file != expected_data_file:
            errors.append(f"{path} coverage data_file must be {expected_data_file}")
        if isinstance(data_file, str):
            seen_data_files.add(data_file)
        if "--cov-fail-under=80" not in pytest_options.get("addopts", ""):
            errors.append(f"{path} must retain the 80 percent pytest coverage gate")
    if len(seen_data_files) != len(EXPECTED_COVERAGE_FILES):
        errors.append("each Python package must use a distinct coverage data file")

    mcp_package = _read_json("packages/mneme-mcp/package.json", repo_root)
    if mcp_package.get("scripts", {}).get("test:coverage") != "vitest run --coverage":
        errors.append("mneme-mcp test:coverage must run vitest with coverage enabled")
    vitest_config = _read("packages/mneme-mcp/vitest.config.ts", repo_root)
    for metric in ("lines", "functions", "branches", "statements"):
        if re.search(rf"^\s*{metric}:\s*80,\s*$", vitest_config, re.MULTILINE) is None:
            errors.append(f"Vitest {metric} coverage threshold must be 80")

    makefile = _read("Makefile", repo_root)
    ci_text = _read(".github/workflows/ci.yml", repo_root)
    release_text = _read(".github/workflows/release.yml", repo_root)
    for data_file in EXPECTED_COVERAGE_FILES.values():
        if data_file not in makefile:
            errors.append(f"Makefile must isolate {data_file}")
        if data_file not in ci_text:
            errors.append(f"ci.yml must isolate {data_file}")
        if data_file not in release_text:
            errors.append(f"release.yml must isolate {data_file}")
    if "test:coverage" not in ci_text or "test:coverage" not in release_text:
        errors.append("CI and release preflight must invoke the Node coverage gate")


#: Paketler-arasi mneme bagimliligi tasiyan pyproject dosyalari.
INTERNAL_DEPENDENTS = (
    "packages/mneme-graph/pyproject.toml",
    "packages/mneme-code/pyproject.toml",
    "packages/mneme-cc-plugin/pyproject.toml",
)

_INTERNAL_DEP = re.compile(r'"(mneme-[a-z]+)\s*>=\s*([0-9][^,"]*)\s*,\s*<\s*([0-9][^"]*)"')


def _check_internal_dependencies(errors: list[str], repo_root: Path) -> None:
    """Every in-repo mneme dependency must admit the version being released.

    version_bump.py keeps the 18 declared version sources in lockstep, but it
    does not touch the constraints packages place on EACH OTHER. Those drifted
    silently across the 4.0 bump: the packages were rebuilt as 4.1.0 while
    still requiring "mneme-core>=3.0.0,<4". Published that way, pip has no
    choice but to resolve mneme-core to the newest 3.x, and a schema-4 reader
    is handed a schema-3 index. The failure surfaces at the user, not here,
    which is exactly why it needs a gate.
    """
    # check_consistency returns (agree, [(label, version), ...]) over all 18
    # sources, so the distinct versions have to be extracted rather than
    # counted. Reading the list length instead silently made this whole check
    # a no-op, which its negative control caught.
    agree, seen = check_consistency()
    versions = {version for _, version in seen}
    if not agree or len(versions) != 1:
        return  # version-source disagreement is already reported elsewhere
    current = next(iter(versions))
    major = current.split(".")[0]
    try:
        next_major = str(int(major) + 1)
    except ValueError:
        errors.append(f"cannot parse major version from {current!r}")
        return

    for rel in INTERNAL_DEPENDENTS:
        path = repo_root / rel
        if not path.is_file():
            errors.append(f"internal dependency source is missing: {rel}")
            continue
        text = path.read_text(encoding="utf-8")
        found = _INTERNAL_DEP.findall(text)
        if not found:
            errors.append(f"{rel} declares no in-repo mneme dependency to check")
            continue
        for name, lower, upper in found:
            if lower != current:
                errors.append(
                    f"{rel}: {name} lower bound is {lower}, expected {current} "
                    "(in-repo dependencies must admit the version being released)"
                )
            if upper.strip() != next_major:
                errors.append(
                    f"{rel}: {name} upper bound is <{upper.strip()}, expected <{next_major}"
                )


def collect_errors(repo_root: Path = REPO_ROOT) -> list[str]:
    errors: list[str] = []
    _check_release_workflows(errors, repo_root)
    _check_version_sources(errors, repo_root)
    _check_tool_registry(errors, repo_root)
    _check_client_manifests(errors, repo_root)
    _check_licenses_and_engines(errors, repo_root)
    _check_coverage_truth(errors, repo_root)
    _check_internal_dependencies(errors, repo_root)

    readme = _read("README.md", repo_root)
    changelog = _read("CHANGELOG.md", repo_root)
    mcp_docs = _read("docs/MCP.md", repo_root)
    for marker in ("v1.0.0-rc", "Hard launch target", "Phase K release"):
        if marker in readme:
            errors.append(f"README still contains stale release marker: {marker}")
    if "9 MCP tools" not in readme:
        errors.append("README must describe lite as nine MCP tools")
    if "mneme upgrade --profile=standard" not in readme:
        errors.append("README must document the supported upgrade command")

    mcp_pkg = _read_json("packages/mneme-mcp/package.json", repo_root)
    server_manifest = _read_json("server.json", repo_root)
    if mcp_pkg.get("mcpName") != IMMUTABLE_MCP_NAME:
        errors.append("mneme-mcp package.json must preserve the immutable registry name")
    if server_manifest.get("name") != mcp_pkg.get("mcpName"):
        errors.append("server.json name must match package.json mcpName exactly")
    if "9 tools" not in mcp_pkg.get("description", ""):
        errors.append("mneme-mcp package.json description must say 9 tools")

    actual_locations = _immutable_name_locations(repo_root)
    if actual_locations != IMMUTABLE_MCP_NAME_LOCATIONS:
        errors.append(
            "immutable MCP name must occur exactly once in each allowlisted file: "
            f"{actual_locations}"
        )

    if "## [1.0.1]" not in changelog or "## [1.0.0]" not in changelog:
        errors.append("CHANGELOG must contain separate 1.0.1 and 1.0.0 sections")
    if not (repo_root / "docs" / "RELEASE.md").is_file():
        errors.append("docs/RELEASE.md release checklist is missing")
    upgrading_path = repo_root / "docs" / "UPGRADING.md"
    if not upgrading_path.is_file():
        errors.append("docs/UPGRADING.md upgrade guide is missing")
    else:
        upgrading = upgrading_path.read_text(encoding="utf-8")
        for needle in ("Apache-2.0", "Node", "summary.json"):
            if needle not in upgrading:
                errors.append(f"docs/UPGRADING.md must cover {needle}")

    for path in _candidate_files(repo_root):
        if path.suffix.lower() != ".md" or path.name == "CHANGELOG.md":
            continue
        text = path.read_text(encoding="utf-8")
        relative = path.relative_to(repo_root)
        if "--upgrade-profile" in text:
            errors.append(f"stale --upgrade-profile docs in {relative}")
        if "six tools" in text.lower():
            errors.append(f"stale six-tools claim in {relative}")

    codes = _error_codes_from_ts(repo_root)
    for code in sorted(codes):
        if f"`{code}`" not in mcp_docs and f'"{code}' not in mcp_docs:
            errors.append(f"docs/MCP.md does not document {code}")
    for stale_code in ("INDEX_NOT_BUILT", "PROFILE_MISMATCH", "PATH_TRAVERSAL", "INTERNAL"):
        if stale_code in mcp_docs:
            errors.append(f"docs/MCP.md still contains stale error code {stale_code}")

    if not (repo_root / "packages/mneme-core/src/mneme_core/__main__.py").is_file():
        errors.append("mneme_core module execution entry point is missing")
    license_text = _read("LICENSE", repo_root)
    if "Apache License" not in license_text or "Version 2.0, January 2004" not in license_text:
        errors.append("LICENSE must contain the verbatim Apache License 2.0 text")
    if not (repo_root / "NOTICE").is_file():
        errors.append("NOTICE file is missing")
    return errors


def main() -> int:
    errors = collect_errors()
    if errors:
        print("Repository integrity check failed:")
        for error in errors:
            print(f"- {error}")
        return 1
    print("Repository integrity check passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
