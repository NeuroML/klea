#!/usr/bin/env python3
"""
Session-scoped path-approval helpers.

Pure helpers for the ``allowed_dirs`` / ``allowed_files`` state used by the
path gate (ADR-0007) and the ``/paths`` command (ADR-0047): resolve and
validate directory approvals, normalise the edited lists, find the entry that
covers a path, and format the user-facing listing.  Command-specific parsing
and messages stay in the app command handler.

See ``devdocs/system/mcp-permissions.md`` and ``system/session-commands.md``.

File: klea_utils/mcp/path_approval.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail DOT com>
"""

import logging
import os
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def resolve_project_root(
    project_root: str | os.PathLike | None = None,
) -> Path:
    """Return the resolved session project root.

    Falls back to the current directory when *project_root* is unset or cannot
    be resolved.

    :param project_root: The session root, or ``None``.
    :returns: A resolved directory path.
    """
    try:
        return Path(project_root or Path.cwd()).expanduser().resolve()
    except (OSError, RuntimeError):
        return Path.cwd().resolve()


def resolve_user_path(value: str | os.PathLike, base: str | os.PathLike) -> Path:
    """Resolve *value* relative to *base* without requiring it to exist.

    :param value: The user-supplied path.
    :param base: The directory relative paths resolve against.
    :returns: A resolved path.
    """
    candidate = Path(value).expanduser()
    if not candidate.is_absolute():
        candidate = Path(base).expanduser() / candidate
    return candidate.resolve(strict=False)


def is_relative_to(path: Path, parent: Path) -> bool:
    """Return whether *path* is inside *parent*."""
    try:
        path.relative_to(parent)
    except ValueError:
        return False
    return True


def normalise_directory(
    project_root: str | os.PathLike | None, value: str | os.PathLike
) -> tuple[str | None, str]:
    """Validate one directory approval.

    The directory must exist, must not be inside the project root (which is
    always permitted), and is returned resolved.

    :param project_root: The session project root.
    :param value: The user-supplied directory.
    :returns: ``(resolved, "")`` on success, or ``(None, error)``.
    """
    try:
        resolved = Path(value).expanduser()
        if not resolved.is_absolute():
            resolved = resolve_project_root(project_root) / resolved
        resolved = resolved.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        return None, f"Cannot resolve {value!r}: {exc}"
    if not resolved.is_dir():
        return None, f"Not a directory: {resolved}"
    root = resolve_project_root(project_root)
    if resolved == root or is_relative_to(resolved, root):
        return None, f"{resolved} is already inside the project root."
    return str(resolved), ""


def normalise_allowed_dirs(
    project_root: str | os.PathLike | None, values: list[Any]
) -> tuple[list[str], list[str]]:
    """Validate edited directory values.

    :param project_root: The session project root.
    :param values: The submitted directory values.
    :returns: ``(accepted, ignored)``; *ignored* holds the validation errors.
    """
    accepted: list[str] = []
    ignored: list[str] = []
    for value in values:
        raw = str(value).strip()
        if not raw:
            continue
        resolved, error = normalise_directory(project_root, raw)
        if resolved is None:
            ignored.append(error)
            continue
        if resolved not in accepted:
            accepted.append(resolved)
    return accepted, ignored


def normalise_allowed_files(current: list[str], values: list[Any]) -> list[str]:
    """Keep only existing sensitive-file approvals that remain selected.

    The editor is additive-disabled for sensitive files: new values are
    ignored, removals are honoured.

    :param current: The current ``allowed_files`` entries.
    :param values: The submitted values.
    :returns: The remaining entries, in their original order.
    """
    selected = {str(value).strip() for value in values if str(value).strip()}
    return [path for path in current if path in selected]


def format_allowed_paths(
    project_root: str | os.PathLike | None,
    allowed_dirs: list[str],
    allowed_files: list[str],
) -> str:
    """Return the user-facing permitted-path listing."""
    root = resolve_project_root(project_root)
    lines = [f"Project root: {root}", "", "Session-allowed directories:"]
    if allowed_dirs:
        lines.extend(f"- {path}" for path in allowed_dirs)
    else:
        lines.append("(none)")
    lines.extend(["", "Session-allowed sensitive files:"])
    if allowed_files:
        lines.extend(f"- {path}" for path in allowed_files)
    else:
        lines.append("(none)")
    return "\n".join(lines)


def entry_matches_path(entry: str, resolved: Path) -> bool:
    """Return whether an approval *entry* resolves to *resolved*."""
    try:
        return str(Path(entry).expanduser().resolve()) == str(resolved)
    except (OSError, RuntimeError):
        return entry == str(resolved)


def find_matching_entry(entries: list[str], resolved: Path) -> str | None:
    """Return the exact approval *entry* matching *resolved*, if any."""
    for entry in entries:
        if entry_matches_path(entry, resolved):
            return entry
    return None


def find_covering_dir(allowed_dirs: list[str], resolved: Path) -> str | None:
    """Return the allowed directory that covers *resolved*, if any."""
    for entry in allowed_dirs:
        try:
            parent = Path(entry).expanduser().resolve()
        except (OSError, RuntimeError):
            continue
        if resolved == parent or is_relative_to(resolved, parent):
            return entry
    return None
