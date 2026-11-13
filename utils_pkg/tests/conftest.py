#!/usr/bin/env python
"""Pytest configuration for the utils_pkg test suite.

NiceGUI reads ``NICEGUI_STORAGE_PATH`` once, when it is first imported, so the
variable must be set here at import time -- before any test module imports
nicegui.  A per-process (and per-xdist-worker) directory outside the repository
keeps each worker the only writer, so NiceGUI's non-recursive ``rmdir`` during
``Storage.clear()`` does not fail on Windows (WinError 145) because other
workers or earlier runs left files behind.

File: tests/conftest.py
"""

# Copyright (C) 2025 Ankur Sinha
# Author: Ankur Sinha <sanjay.ankur@gmail.com>

import os
import shutil
import tempfile
from pathlib import Path

import pytest

_worker = os.environ.get("PYTEST_XDIST_WORKER", "main")
_storage_path = Path(tempfile.gettempdir()) / f"klea-nicegui-{os.getpid()}-{_worker}"

# Start from a clean directory: a stale one from an earlier or crashed run must
# never leak into this session.
shutil.rmtree(_storage_path, ignore_errors=True)
os.environ["NICEGUI_STORAGE_PATH"] = str(_storage_path)


def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    """Remove this worker's scratch storage directory at session end."""
    shutil.rmtree(_storage_path, ignore_errors=True)
