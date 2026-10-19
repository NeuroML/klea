#!/usr/bin/env python3
"""
Session-wide test setup for the agent package.

NiceGUI reads ``NICEGUI_STORAGE_PATH`` once, when it is first imported, so
this must run before any test module imports it.  Each pytest-xdist worker
gets its own storage directory: NiceGUI's reset at test teardown deletes the
directory, which races with other workers writing to a shared one.  The
path is always overwritten because workers inherit the controller's
environment.

File: tests/conftest.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail DOT com>
"""

import os
import tempfile
from pathlib import Path

_worker = os.environ.get("PYTEST_XDIST_WORKER", "main")
os.environ["NICEGUI_STORAGE_PATH"] = str(
    Path(tempfile.gettempdir()) / f"klea-nicegui-{os.getpid()}-{_worker}"
)
