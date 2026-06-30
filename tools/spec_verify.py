"""Critical-path hook verifier.

Implements the C3 sacred-constraint enforcement promised by
``docs/CONSTRAINTS.md``: SessionStart, Stop, and PreCompact hooks must
not import any LLM client, HTTP client, or network library. This
script parses every hook module under
``packages/mneme-cc-plugin/src/mneme_cc_plugin/hooks/`` with the
``ast`` module (more reliable than grep) and fails when a forbidden
import is found.

Exit codes:
  0 - all critical hooks clean.
  1 - one or more forbidden imports detected.
  2 - usage error (no hooks directory or unreadable file).

The set of forbidden import roots is intentionally conservative.
Adding a new client library that the C3 hooks must never reach is a
one-line change to ``FORBIDDEN_ROOTS``. Hook implementations that
absolutely must reference a forbidden name can suppress the check on
that exact line with an inline ``# pragma: no cover (offline-only)``
comment, mirroring the rationale documented in CONSTRAINTS.md.
"""

from __future__ import annotations

import ast
import sys
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
HOOKS_DIR = (
    REPO_ROOT
    / "packages"
    / "mneme-cc-plugin"
    / "src"
    / "mneme_cc_plugin"
    / "hooks"
)

CRITICAL_HOOKS: frozenset[str] = frozenset(
    {
        "session_start",
        "stop",
        "pre_compact",
        # session_end and post_tool_use also execute on the live session path,
        # so the C3 no-network import scan covers them too (defense in depth
        # alongside the transitive runtime no-network test).
        "session_end",
        "post_tool_use",
        # user_prompt_submit fires on every user prompt and is the CCE trigger
        # hook. It runs on the live session path, so the C3 scan covers it too.
        "user_prompt_submit",
    }
)

FORBIDDEN_ROOTS: frozenset[str] = frozenset(
    {
        "anthropic",
        "openai",
        "requests",
        "httpx",
        "urllib.request",
        "urllib3",
        "aiohttp",
    }
)

PRAGMA_TOKEN = "pragma: no cover (offline-only)"


@dataclass
class Violation:
    file: Path
    line: int
    name: str


def _read_line(path: Path, lineno: int) -> str:
    try:
        with path.open("r", encoding="utf-8") as fp:
            for i, raw in enumerate(fp, start=1):
                if i == lineno:
                    return raw.rstrip("\n")
    except OSError:
        pass
    return ""


def _has_pragma(path: Path, lineno: int) -> bool:
    return PRAGMA_TOKEN in _read_line(path, lineno)


def _scan(path: Path) -> list[Violation]:
    found: list[Violation] = []
    try:
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(path))
    except OSError as exc:
        print(f"::error::Unreadable hook file {path}: {exc}", file=sys.stderr)
        return found

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                root = alias.name.split(".")[0]
                if root in FORBIDDEN_ROOTS and not _has_pragma(path, node.lineno):
                    found.append(Violation(path, node.lineno, alias.name))
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            root = module.split(".")[0]
            if root in FORBIDDEN_ROOTS and not _has_pragma(path, node.lineno):
                found.append(Violation(path, node.lineno, module))
    return found


def main() -> int:
    if not HOOKS_DIR.is_dir():
        print(f"::error::Hooks directory not found: {HOOKS_DIR}", file=sys.stderr)
        return 2

    violations: list[Violation] = []
    for stem in sorted(CRITICAL_HOOKS):
        target = HOOKS_DIR / f"{stem}.py"
        if not target.is_file():
            print(
                f"::error::Critical hook missing: {target}", file=sys.stderr
            )
            return 2
        violations.extend(_scan(target))

    if violations:
        for v in violations:
            print(
                f"::error file={v.file},line={v.line}::"
                f"C3 violation: critical-path hook imports forbidden module '{v.name}'"
            )
        print(
            f"spec_verify FAILED: {len(violations)} forbidden imports "
            f"across {len(CRITICAL_HOOKS)} critical hooks.",
            file=sys.stderr,
        )
        return 1

    print(
        f"spec_verify PASSED: {len(CRITICAL_HOOKS)} critical hooks scanned "
        f"against {len(FORBIDDEN_ROOTS)} forbidden roots."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
