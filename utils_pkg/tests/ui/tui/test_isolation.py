#!/usr/bin/env python3
"""
Invariant I8 guard: the shared TUI imports no app package.

``klea_utils/ui/tui`` is app-agnostic; the per-app entries
(``klea_agent`` / ``klea_rag``) compose it via the documented context slots
(ADR-0031/ADR-0050).  This test fails if any shared TUI module grows an
import of an app package, which would break the one-way dependency.

File: tests/ui/tui/test_isolation.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail DOT com>
"""

from __future__ import annotations

import ast
from pathlib import Path

import klea_utils.ui.tui as tui_pkg

#: App packages the shared TUI must never import (invariant I8).
APP_PACKAGES = ("klea_agent", "klea_rag", "neuroml_mcp")


def _imported_modules(path: Path) -> set[str]:
    """Return the module names imported by *path* (import + from-import)."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
    return modules


def test_shared_tui_imports_no_app_package():
    """No module under ``klea_utils/ui/tui`` imports an app package."""
    root = Path(tui_pkg.__file__).parent
    offenders = [
        (str(path.relative_to(root)), module)
        for path in root.rglob("*.py")
        for module in _imported_modules(path)
        if module.split(".")[0] in APP_PACKAGES
    ]
    assert not offenders, f"app imports in the shared TUI: {offenders}"
