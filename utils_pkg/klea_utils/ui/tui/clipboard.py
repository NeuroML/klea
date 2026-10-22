#!/usr/bin/env python3
"""
System clipboard fallback for the Klea TUI.

Textual's :meth:`textual.app.App.copy_to_clipboard` writes OSC 52 and keeps
an internal copy.  Some terminals do not support OSC 52 (or disable it), so
this module also tries the platform clipboard tools when they are available.
It is best-effort and never raises: the app can still use its internal
clipboard when no backend works.

File: klea_utils/ui/tui/clipboard.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail DOT com>
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import sys

logger = logging.getLogger(__name__)

#: Backend command that last succeeded, tried first on later copies.
_cached_backend: list[str] | None = None

#: Per-command copy timeout.  Clipboard helpers should return immediately;
#: this bounds a broken display backend so the UI does not stall.
_COPY_TIMEOUT_S = 1.0


def _candidate_backends() -> list[list[str]]:
    """Return clipboard commands to try, most appropriate first.

    :returns: Command argument lists, without checking whether they exist.
    """
    if sys.platform == "darwin":
        return [["pbcopy"]]
    if os.name == "nt":
        return [["clip"]]

    candidates: list[list[str]] = []
    if os.environ.get("WAYLAND_DISPLAY"):
        candidates.append(["wl-copy"])
    candidates.extend(
        [
            ["xclip", "-selection", "clipboard"],
            ["xsel", "--clipboard", "--input"],
        ]
    )
    if not os.environ.get("WAYLAND_DISPLAY"):
        # Wayland without the environment variable (or XWayland) may still
        # have wl-copy; keep it as a last resort.
        candidates.append(["wl-copy"])
    return candidates


def _run_backend(command: list[str], text: str) -> bool:
    """Run one clipboard command with *text* on stdin.

    :param command: Command and arguments.
    :param text: Text to copy.
    :returns: ``True`` when the command exited successfully.
    """
    try:
        result = subprocess.run(
            command,
            input=text.encode("utf-8"),
            check=False,
            timeout=_COPY_TIMEOUT_S,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        logger.debug(f"Clipboard backend {command[0]} failed: {exc}")
        return False
    if result.returncode != 0:
        logger.debug(f"Clipboard backend {command[0]} exited {result.returncode}")
        return False
    return True


def copy_to_system_clipboard(text: str) -> bool:
    """Copy *text* to the OS clipboard with a platform tool, if possible.

    Tries the last successful backend first, then the platform candidates.
    The app's OSC 52 copy remains the primary path; this function only adds a
    fallback for terminals that ignore OSC 52.  It never raises.

    :param text: Text to copy.
    :returns: ``True`` when a system clipboard command succeeded.
    """
    global _cached_backend
    if not text:
        return False

    if _cached_backend is not None:
        if _run_backend(_cached_backend, text):
            return True
        _cached_backend = None

    for command in _candidate_backends():
        if shutil.which(command[0]) is None:
            continue
        if _run_backend(command, text):
            _cached_backend = command
            logger.debug(f"Copied text using {command[0]}")
            return True
    logger.warning("No working system clipboard backend found")
    return False
