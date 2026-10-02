#!/usr/bin/env python3
"""
Contract test: source must not swallow cancellation.

A graph run is stopped by cancelling the asyncio task that drives it, which
raises ``CancelledError`` at the node/tool's next ``await``.  A handler that
catches ``BaseException`` (or ``CancelledError``, or a bare ``except``)
without re-raising turns that cancellation into an ordinary result, so the
run keeps going after the user asked it to stop.

This test statically scans the package sources for such handlers.  A handler
that re-raises (bare ``raise`` or ``raise`` of the exception) is fine, and a
handler may opt out with an inline ``cancellation-contract: exempt`` comment
for the rare dev-only case that must never abort (e.g. diagram export).

File: utils_pkg/tests/test_cancellation_contract.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail DOT com>
"""

import ast
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

#: Package source roots to scan (relative to the repo root).  ``tests``
#: directories are skipped: test code may deliberately exercise cancellation.
PACKAGE_ROOTS = (
    "utils_pkg/klea_utils",
    "agent_pkg/klea_agent",
    "rag_pkg/klea_rag",
    "mcp_pkg/neuroml_mcp",
)

#: Inline marker that exempts a handler from the contract.
EXEMPT_MARKER = "cancellation-contract: exempt"

#: How many lines above the ``except`` to look for the exemption marker.
_EXEMPT_LOOKBACK = 3


def _iter_source_files() -> list[Path]:
    """Return the package source files to scan, tests excluded."""
    files: list[Path] = []
    for rel in PACKAGE_ROOTS:
        for path in sorted((REPO_ROOT / rel).rglob("*.py")):
            if "tests" in path.parts:
                continue
            files.append(path)
    return files


def _handler_type_names(handler: ast.ExceptHandler) -> list[str]:
    """Return the textual exception type(s) an ``except`` handler catches."""
    node = handler.type
    if node is None:
        return ["<bare except>"]
    if isinstance(node, ast.Name):
        return [node.id]
    if isinstance(node, ast.Attribute):
        return [ast.unparse(node)]
    if isinstance(node, ast.Tuple):
        return [ast.unparse(element) for element in node.elts]
    return [ast.unparse(node)]


def _handles_cancellation(names: list[str]) -> bool:
    """Whether the caught type names include cancellation/base exceptions."""
    return any(
        name == "BaseException"
        or name == "<bare except>"
        or name.endswith("CancelledError")
        for name in names
    )


def _body_reraises(handler: ast.ExceptHandler) -> bool:
    """Whether the handler body contains a ``raise`` (re-raise is allowed)."""
    return any(isinstance(node, ast.Raise) for node in ast.walk(handler))


def _is_exempt(source_lines: list[str], lineno: int) -> bool:
    """Whether an exemption marker sits on/above the handler's ``except``."""
    start = max(0, lineno - 1 - _EXEMPT_LOOKBACK)
    window = source_lines[start:lineno]
    return any(EXEMPT_MARKER in line for line in window)


def _scan(path: Path) -> list[str]:
    """Return one finding string per cancellation-swallowing handler."""
    source = path.read_text(encoding="utf-8")
    source_lines = source.splitlines()
    tree = ast.parse(source, filename=str(path))
    findings: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.ExceptHandler):
            continue
        names = _handler_type_names(node)
        if not _handles_cancellation(names):
            continue
        if _body_reraises(node):
            continue
        if _is_exempt(source_lines, node.lineno):
            continue
        rel = path.relative_to(REPO_ROOT)
        findings.append(
            f"{rel}:{node.lineno} catches {', '.join(names)} without re-raising"
        )
    return findings


def test_source_does_not_swallow_cancellation() -> None:
    """No package handler may catch cancellation (or BaseException) silently."""
    findings: list[str] = []
    for path in _iter_source_files():
        findings.extend(_scan(path))
    assert not findings, (
        "Cancellation-swallowing exception handlers found.  Catch Exception "
        "(not BaseException/CancelledError), re-raise, or add a "
        f"'{EXEMPT_MARKER}' comment:\n" + "\n".join(findings)
    )
