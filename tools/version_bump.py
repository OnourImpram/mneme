"""Atomic version bump across all release version sources.

Release infrastructure. mneme has package metadata, plugin
manifests, runtime constants, and marketplace metadata that must move
together:

1. ``packages/mneme-core/pyproject.toml``         (PEP 440)
2. ``packages/mneme-mcp/package.json``            (SemVer)
3. ``packages/mneme-cc-plugin/pyproject.toml``    (SemVer-style),
   plus the ``mneme-graph`` and ``mneme-code`` pyprojects (PEP 440).
4. ``packages/mneme-cc-plugin/plugin.json``       (SemVer)
5. ``package.json`` (workspace root)              (SemVer)
6. runtime constants, Codex plugin manifest, Claude plugin manifest,
   and Claude plugin marketplace entry.
7. ``CITATION.cff`` (YAML ``version:`` field) for Zenodo/citation metadata.
8. ``README.md`` status line (prose SemVer claim, kept honest in lockstep).
9. ``server.json`` MCP Registry manifest (top-level + package entry).

The script accepts a SemVer string (``1.0.0``, ``1.0.0-rc.1``,
``1.0.0-alpha.0``) and writes the PEP 440 equivalent to the
mneme-core pyproject and the SemVer literal to the remaining sources.

PEP 440 mapping:

- ``1.0.0`` <-> ``1.0.0``
- ``1.0.0-alpha.N`` <-> ``1.0.0a{N}``
- ``1.0.0-beta.N`` <-> ``1.0.0b{N}``
- ``1.0.0-rc.N`` <-> ``1.0.0rc{N}``

Usage:

  py -3 tools/version_bump.py 1.0.0-rc.1
  py -3 tools/version_bump.py --check 1.0.0   # target assertion mode
  py -3 tools/version_bump.py --check         # all sources agree

In ``--check`` mode the script reads every source, confirms they all
resolve to the same canonical SemVer, and exits 0 on agreement or 1
on divergence with a structured report.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path

SEMVER_RE = re.compile(
    r"^(\d+)\.(\d+)\.(\d+)(?:-(alpha|beta|rc)\.(\d+))?$"
)
PEP440_RE = re.compile(
    r"^(\d+)\.(\d+)\.(\d+)(?:(a|b|rc)(\d+))?$"
)

REPO_ROOT = Path(__file__).resolve().parents[1]


JsonPathPart = str | int


@dataclass(frozen=True)
class VersionSource:
    label: str
    path: Path
    flavor: str
    # Used by JSON sources: key under root object holding the version.
    json_key: str | None = None
    # Used by JSON sources when the version is nested.
    json_path: tuple[JsonPathPart, ...] | None = None
    # Used by py-runtime / ts-runtime: the matching regex anchor.
    # The regex must capture the version literal in group 1.
    runtime_pattern: str | None = None


# Build + metadata version sources. ``pep440`` and ``semver-*`` flavors
# cover packaging declaration sites; ``pep440-pystr``, ``semver-pystr``,
# and ``semver-tsconst`` cover runtime constants. Plugin and marketplace
# manifest sources keep the install surfaces aligned with package metadata.
SOURCES: tuple[VersionSource, ...] = (
    VersionSource(
        label="mneme-core pyproject",
        path=REPO_ROOT / "packages" / "mneme-core" / "pyproject.toml",
        flavor="pep440",
    ),
    VersionSource(
        label="mneme-mcp package.json",
        path=REPO_ROOT / "packages" / "mneme-mcp" / "package.json",
        flavor="semver-json",
        json_key="version",
    ),
    VersionSource(
        label="mneme-cc-plugin pyproject",
        path=REPO_ROOT / "packages" / "mneme-cc-plugin" / "pyproject.toml",
        flavor="semver-toml",
    ),
    VersionSource(
        label="mneme-graph pyproject",
        path=REPO_ROOT / "packages" / "mneme-graph" / "pyproject.toml",
        flavor="pep440",
    ),
    VersionSource(
        label="mneme-code pyproject",
        path=REPO_ROOT / "packages" / "mneme-code" / "pyproject.toml",
        flavor="pep440",
    ),
    VersionSource(
        label="mneme-cc-plugin plugin.json",
        path=REPO_ROOT / "packages" / "mneme-cc-plugin" / "plugin.json",
        flavor="semver-json",
        json_key="version",
    ),
    VersionSource(
        label="mneme-cc-plugin native .claude-plugin manifest",
        path=(
            REPO_ROOT
            / "packages"
            / "mneme-cc-plugin"
            / ".claude-plugin"
            / "plugin.json"
        ),
        flavor="semver-json",
        json_key="version",
    ),
    VersionSource(
        label="mneme-codex-plugin manifest",
        path=(
            REPO_ROOT
            / "packages"
            / "mneme-codex-plugin"
            / ".codex-plugin"
            / "plugin.json"
        ),
        flavor="semver-json",
        json_key="version",
    ),
    VersionSource(
        label="Claude marketplace entry",
        path=REPO_ROOT / ".claude-plugin" / "marketplace.json",
        flavor="semver-json",
        json_path=("plugins", 0, "version"),
    ),
    VersionSource(
        label="root package.json",
        path=REPO_ROOT / "package.json",
        flavor="semver-json",
        json_key="version",
    ),
    # Runtime constants: importable from the published packages and
    # reported through `mneme --version`, `mneme_core.__version__`,
    # and the MCP server handshake. Pinning these to the same script
    # closes the runtime/packaging drift Codex Pass 1 flagged.
    VersionSource(
        label="mneme-core __version__",
        path=(
            REPO_ROOT
            / "packages"
            / "mneme-core"
            / "src"
            / "mneme_core"
            / "__init__.py"
        ),
        flavor="pep440-pystr",
        runtime_pattern=r'(?m)^(__version__\s*=\s*)"([^"]+)"',
    ),
    VersionSource(
        label="mneme-cc-plugin __version__",
        path=(
            REPO_ROOT
            / "packages"
            / "mneme-cc-plugin"
            / "src"
            / "mneme_cc_plugin"
            / "__init__.py"
        ),
        flavor="semver-pystr",
        runtime_pattern=r'(?m)^(__version__\s*=\s*)"([^"]+)"',
    ),
    VersionSource(
        label="mneme-mcp SERVER_VERSION",
        path=REPO_ROOT / "packages" / "mneme-mcp" / "src" / "index.ts",
        flavor="semver-tsconst",
        runtime_pattern=r'(const\s+SERVER_VERSION\s*=\s*)"([^"]+)"',
    ),
    # Citation metadata. Kept in lockstep so the Zenodo/CITATION record never
    # drifts from the package version again (the 1.0.2/1.0.3 split that the
    # fifth review caught originated here, when CITATION.cff was not a source).
    VersionSource(
        label="CITATION.cff",
        path=REPO_ROOT / "CITATION.cff",
        flavor="semver-yaml",
    ),
    # Antigravity (Gemini extension) manifest. Keeps the install surface
    # aligned with the rest of the release sources.
    VersionSource(
        label="mneme-antigravity-plugin manifest",
        path=(
            REPO_ROOT
            / "packages"
            / "mneme-antigravity-plugin"
            / "gemini-extension.json"
        ),
        flavor="semver-json",
        json_key="version",
    ),
    VersionSource(
        label="MCP Registry server.json (top-level)",
        path=REPO_ROOT / "server.json",
        flavor="semver-json",
        json_key="version",
    ),
    VersionSource(
        label="MCP Registry server.json (package entry)",
        path=REPO_ROOT / "server.json",
        flavor="semver-json",
        json_path=("packages", 0, "version"),
    ),
    VersionSource(
        label="README status line",
        path=REPO_ROOT / "README.md",
        flavor="semver-prose",
        runtime_pattern=(
            r"(\*\*Status\*\*: )"
            r"(\d+\.\d+\.\d+(?:-(?:alpha|beta|rc)\.\d+)?)"
            r"(?= public release)"
        ),
    ),
)


def _read_json_path(data: object, path: tuple[JsonPathPart, ...]) -> object:
    current = data
    for part in path:
        if isinstance(part, int):
            if not isinstance(current, list):
                raise ValueError(f"Expected list before index {part}")
            current = current[part]
        else:
            if not isinstance(current, dict):
                raise ValueError(f"Expected object before key {part}")
            current = current[part]
    return current


def _write_json_path(
    data: object,
    path: tuple[JsonPathPart, ...],
    value: str,
) -> None:
    if not path:
        raise ValueError("JSON path cannot be empty")
    current = data
    for part in path[:-1]:
        if isinstance(part, int):
            if not isinstance(current, list):
                raise ValueError(f"Expected list before index {part}")
            current = current[part]
        else:
            if not isinstance(current, dict):
                raise ValueError(f"Expected object before key {part}")
            current = current[part]
    last = path[-1]
    if isinstance(last, int):
        if not isinstance(current, list):
            raise ValueError(f"Expected list before index {last}")
        current[last] = value
    else:
        if not isinstance(current, dict):
            raise ValueError(f"Expected object before key {last}")
        current[last] = value


def semver_to_pep440(semver: str) -> str:
    m = SEMVER_RE.match(semver)
    if not m:
        raise ValueError(f"Not a valid SemVer: {semver}")
    major, minor, patch, kind, num = m.groups()
    if kind is None:
        return f"{major}.{minor}.{patch}"
    short = {"alpha": "a", "beta": "b", "rc": "rc"}[kind]
    return f"{major}.{minor}.{patch}{short}{num}"


def pep440_to_semver(pep440: str) -> str:
    m = PEP440_RE.match(pep440)
    if not m:
        raise ValueError(f"Not a valid PEP 440 string: {pep440}")
    major, minor, patch, kind, num = m.groups()
    if kind is None:
        return f"{major}.{minor}.{patch}"
    long_form = {"a": "alpha", "b": "beta", "rc": "rc"}[kind]
    return f"{major}.{minor}.{patch}-{long_form}.{num}"


def read_version(source: VersionSource) -> str:
    text = source.path.read_text(encoding="utf-8")
    if source.flavor == "pep440":
        m = re.search(r'^version\s*=\s*"([^"]+)"', text, re.MULTILINE)
        if not m:
            raise ValueError(f"No version field in {source.label}")
        return pep440_to_semver(m.group(1))
    if source.flavor == "semver-toml":
        m = re.search(r'^version\s*=\s*"([^"]+)"', text, re.MULTILINE)
        if not m:
            raise ValueError(f"No version field in {source.label}")
        return m.group(1)
    if source.flavor == "semver-yaml":
        # CITATION.cff: bare ``version: X.Y.Z`` YAML scalar (optionally quoted).
        # The anchored ``^version:`` never matches line 1's ``cff-version:``.
        m = re.search(
            r"""^version:[ \t]*["']?([^"'\n]+)["']?[ \t]*$""",
            text,
            re.MULTILINE,
        )
        if not m:
            raise ValueError(f"No version field in {source.label}")
        return m.group(1)
    if source.flavor in {"semver-json"}:
        data = json.loads(text)
        if source.json_path is not None:
            version = _read_json_path(data, source.json_path)
        else:
            assert source.json_key is not None
            version = data[source.json_key]
        if not isinstance(version, str):
            raise ValueError(f"Version value in {source.label} is not a string")
        return version
    if source.flavor in {
        "pep440-pystr",
        "semver-pystr",
        "semver-tsconst",
        "semver-prose",
    }:
        assert source.runtime_pattern is not None
        m = re.search(source.runtime_pattern, text)
        if not m:
            raise ValueError(f"No version literal in {source.label}")
        literal = m.group(2)
        if source.flavor == "pep440-pystr":
            return pep440_to_semver(literal)
        return literal
    raise ValueError(f"Unknown flavor {source.flavor!r}")


def write_version(source: VersionSource, new_semver: str) -> None:
    text = source.path.read_text(encoding="utf-8")
    if source.flavor == "pep440":
        new = semver_to_pep440(new_semver)
        updated = re.sub(
            r'^(version\s*=\s*)"[^"]+"',
            f'\\1"{new}"',
            text,
            count=1,
            flags=re.MULTILINE,
        )
    elif source.flavor == "semver-toml":
        updated = re.sub(
            r'^(version\s*=\s*)"[^"]+"',
            f'\\1"{new_semver}"',
            text,
            count=1,
            flags=re.MULTILINE,
        )
    elif source.flavor == "semver-yaml":
        updated = re.sub(
            r"""^(version:[ \t]*)["']?[^"'\n]+["']?[ \t]*$""",
            f"\\g<1>{new_semver}",
            text,
            count=1,
            flags=re.MULTILINE,
        )
        if updated == text:
            raise ValueError(f"No replacement performed in {source.label}")
    elif source.flavor == "semver-json":
        data = json.loads(text)
        if source.json_path is not None:
            _write_json_path(data, source.json_path, new_semver)
        else:
            assert source.json_key is not None
            data[source.json_key] = new_semver
        updated = json.dumps(data, indent=2, ensure_ascii=False) + "\n"
    elif source.flavor in {"pep440-pystr", "semver-pystr", "semver-tsconst"}:
        assert source.runtime_pattern is not None
        literal = (
            semver_to_pep440(new_semver)
            if source.flavor == "pep440-pystr"
            else new_semver
        )
        # Replace the captured group's literal in-place; group(1)
        # preserves the assignment prefix.
        updated = re.sub(
            source.runtime_pattern,
            lambda m: f'{m.group(1)}"{literal}"',
            text,
            count=1,
        )
        if updated == text:
            raise ValueError(f"No replacement performed in {source.label}")
    elif source.flavor == "semver-prose":
        # Unquoted prose literal: group(1) keeps the prefix, the
        # lookahead in the pattern keeps the trailing text untouched.
        assert source.runtime_pattern is not None
        updated = re.sub(
            source.runtime_pattern,
            lambda m: f"{m.group(1)}{new_semver}",
            text,
            count=1,
        )
        if updated == text:
            raise ValueError(f"No replacement performed in {source.label}")
    else:
        raise ValueError(f"Unknown flavor {source.flavor!r}")
    source.path.write_text(updated, encoding="utf-8", newline="")


def check_consistency() -> tuple[bool, list[tuple[str, str]]]:
    """Returns (all_agree, [(label, version)]). Always reports actual values."""
    seen: list[tuple[str, str]] = []
    for src in SOURCES:
        try:
            seen.append((src.label, read_version(src)))
        except (OSError, ValueError) as exc:
            seen.append((src.label, f"ERROR: {exc}"))
    versions = {v for _, v in seen if not v.startswith("ERROR:")}
    return len(versions) == 1, seen


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "version",
        nargs="?",
        help="Target SemVer (e.g. 1.0.0 or 1.0.0-rc.1)",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Verify all sources already declare this version. Exit 1 on drift.",
    )
    args = parser.parse_args()

    if args.version is None and not args.check:
        parser.error("version is required unless --check is used")
    if args.version is not None and not SEMVER_RE.match(args.version):
        print(
            f"ERROR: '{args.version}' is not a valid SemVer "
            f"(expected MAJOR.MINOR.PATCH[-alpha|beta|rc.N]).",
            file=sys.stderr,
        )
        return 2

    if args.check:
        agree, seen = check_consistency()
        report = {
            "target": args.version,
            "agree": agree,
            "sources": [
                {"label": label, "version": version} for label, version in seen
            ],
        }
        match = (
            True
            if args.version is None
            else all(v == args.version for _, v in seen if not v.startswith("ERROR:"))
        )
        report["matches_target"] = match
        print(json.dumps(report, indent=2, ensure_ascii=False))
        return 0 if (agree and match) else 1

    assert args.version is not None
    for src in SOURCES:
        write_version(src, args.version)
    agree, seen = check_consistency()
    print(json.dumps(
        {
            "bumped_to": args.version,
            "agree_after_write": agree,
            "sources": [
                {"label": label, "version": version} for label, version in seen
            ],
        },
        indent=2,
        ensure_ascii=False,
    ))
    return 0 if agree else 1


if __name__ == "__main__":
    raise SystemExit(main())
