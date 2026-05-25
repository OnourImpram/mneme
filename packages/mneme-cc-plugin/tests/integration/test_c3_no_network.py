"""Runtime transitive C3 no-network guard.

``tools/spec_verify.py`` AST-scans only the three critical hook leaf
files for forbidden imports.  It does NOT follow their import closure.
A forbidden import reached three modules deep through ``mneme_core``
would pass the static check while violating C3 at runtime.

This test closes that gap by patching ``builtins.__import__`` so that
every import statement executed while loading a hook module — including
all transitive ``mneme_core.*`` sub-imports — routes through the guard.
A forbidden top-level package name anywhere in the closure raises
``AssertionError``, making the test go red exactly when the static gate
would stay green.
"""

from __future__ import annotations

import builtins
import sys
from collections.abc import Callable
from typing import Any

import pytest

# ---------------------------------------------------------------------------
# Forbidden roots — mirrors FORBIDDEN_ROOTS in tools/spec_verify.py minus
# the stdlib ``urllib``; ``urllib3`` is the third-party library.
# ---------------------------------------------------------------------------
FORBIDDEN: frozenset[str] = frozenset(
    {
        "httpx",
        "anthropic",
        "openai",
        "requests",
        "urllib3",
        "aiohttp",
        "httpcore",
    }
)

# Fully-qualified module names for the three critical hook leaf files.
CRITICAL_HOOKS: list[str] = [
    "mneme_cc_plugin.hooks.session_start",
    "mneme_cc_plugin.hooks.stop",
    "mneme_cc_plugin.hooks.pre_compact",
]


def _make_guard(real_import: Callable[..., Any]) -> Callable[..., Any]:
    """Return a ``__import__`` replacement that blocks FORBIDDEN roots."""

    def _guard(
        name: str,
        globals: dict[str, Any] | None = None,
        locals: dict[str, Any] | None = None,
        fromlist: tuple[str, ...] = (),
        level: int = 0,
    ) -> Any:
        root = name.split(".")[0]
        if root in FORBIDDEN:
            raise AssertionError(
                f"C3 violation: transitive import of forbidden module '{name}' "
                f"(root '{root}') detected in critical hook closure"
            )
        return real_import(name, globals, locals, fromlist, level)

    return _guard


def _evict_plugin_and_core(monkeypatch: pytest.MonkeyPatch) -> None:
    """Remove all ``mneme_cc_plugin.*`` and ``mneme_core.*`` entries from
    ``sys.modules`` so the next import re-executes every module body and
    its transitive imports, routing all ``import`` statements through the
    patched ``builtins.__import__``."""
    keys = [
        k
        for k in list(sys.modules)
        if k == "mneme_cc_plugin"
        or k.startswith("mneme_cc_plugin.")
        or k == "mneme_core"
        or k.startswith("mneme_core.")
    ]
    for key in keys:
        monkeypatch.delitem(sys.modules, key, raising=False)


@pytest.mark.parametrize("module_name", CRITICAL_HOOKS)
def test_hook_closure_has_no_forbidden_imports(
    module_name: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Importing the hook module (and its full transitive closure) must
    not reach any forbidden network library.

    The guard wraps the real ``builtins.__import__`` so that every
    ``import`` statement executed during module loading — not just
    top-level hook imports — is inspected.  ``importlib.import_module``
    is intentionally NOT used here because it bypasses
    ``builtins.__import__`` and the guard would never fire.
    ``monkeypatch`` auto-restores both ``builtins.__import__`` and
    ``sys.modules`` after each test, keeping tests independent.
    """
    _evict_plugin_and_core(monkeypatch)

    real_import = builtins.__import__
    monkeypatch.setattr(builtins, "__import__", _make_guard(real_import))

    # This single call re-executes the hook module body plus every
    # transitive import that has been evicted from sys.modules, all
    # routed through the guard above.
    builtins.__import__(module_name)
