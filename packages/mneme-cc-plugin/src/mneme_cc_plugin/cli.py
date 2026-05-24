"""Top-level entry point for the ``mneme`` console script.

This module exists so ``project.scripts.mneme`` in ``pyproject.toml``
can stay short. The actual CLI logic lives in
``mneme_cc_plugin.install.cli``.
"""

from __future__ import annotations

from mneme_cc_plugin.install.cli import main

__all__ = ["main"]
