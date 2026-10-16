#!/usr/bin/env python3
"""
Best-effort process-title setting for Klea processes.

A Python process shows up as ``python`` in ``ps``/``top``/terminal titles,
which makes it hard to tell Klea's processes apart.  :func:`set_process_title`
sets a friendlier name: it uses ``setproctitle`` (which rewrites ``argv``) when
that optional package is installed, and otherwise falls back to the kernel's
per-process ``comm`` via ``prctl(PR_SET_NAME)`` on Linux (limited to 15 chars,
as shown by ``ps -o comm`` / ``top``).  It is a no-op elsewhere and never
raises.

The TUI, web UI and REPL entries call it directly with their ``app_name``.  A
server spawned by a client is launched as a ``uvicorn`` subprocess, so the
launcher passes the intended title through the ``KLEA_PROC_TITLE`` environment
variable, which :func:`klea_utils.api.app.make_app` applies at startup.

File: klea_utils/proctitle.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail DOT com>
"""

from __future__ import annotations

import importlib
import logging
import sys

logger = logging.getLogger(__name__)

#: ``prctl`` option that sets the process's ``comm`` (Linux).
_PR_SET_NAME = 15

#: Linux caps ``comm`` at 15 bytes plus the terminator.
_COMM_MAX_BYTES = 15


def set_process_title(name: str) -> None:
    """Best-effort set the OS process title to *name*.

    Tries ``setproctitle`` first (full ``argv`` rewrite); on Linux without it,
    sets the kernel ``comm`` via ``prctl``.  A no-op on other platforms, for an
    empty *name*, or when the underlying call is unavailable.  Never raises.

    :param name: The title to show (e.g. ``"klea-agent-tui"``).
    """
    if not name:
        return

    try:
        setproctitle = importlib.import_module("setproctitle")
    except ImportError:
        pass
    else:
        try:
            setproctitle.setproctitle(name)
            return
        except Exception as exc:  # noqa: BLE001
            logger.debug(f"setproctitle failed: {exc = }")

    if sys.platform.startswith("linux"):
        try:
            import ctypes

            libc = ctypes.CDLL(None)
            libc.prctl(_PR_SET_NAME, name.encode()[:_COMM_MAX_BYTES], 0, 0, 0)
        except Exception as exc:  # noqa: BLE001
            logger.debug(f"prctl(PR_SET_NAME) failed: {exc = }")
