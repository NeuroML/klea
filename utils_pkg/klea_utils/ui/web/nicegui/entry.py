#!/usr/bin/env python3
"""
Shared NiceGUI web-entry helpers for Klea apps.

These helpers are intentionally free of any ``nicegui`` import so an
app's ``ui/web/app.py`` can call them *before* the NiceGUI machinery is
imported (NiceGUI reads ``NICEGUI_STORAGE_PATH`` at import time).

File: klea_utils/ui/web/nicegui/entry.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail DOT com>
"""

import logging
import os
import sys
from pathlib import Path

logger = logging.getLogger(__name__)


def app_name_from_argv(default: str) -> str:
    """Return the ``--app-name`` flag value from ``sys.argv``, else *default*.

    :param default: App name to fall back to.
    :returns: The parsed ``--app-name`` value, or *default*.
    """
    if "--app-name" in sys.argv:
        try:
            return sys.argv[sys.argv.index("--app-name") + 1]
        except (IndexError, ValueError):
            return default
    return default


def default_storage_env(app_name: str) -> str:
    """Point ``NICEGUI_STORAGE_PATH`` at the per-app data dir when unset.

    Called at the very top of each app's ``ui/web/app.py``, before
    anything imports NiceGUI: ``nicegui/storage.py`` honours
    ``NICEGUI_STORAGE_PATH`` at import time, so setting it early gives
    the storage subsystem the correct per-app directory from the start.

    :param app_name: App name (overridable via ``--app-name``) used for
        the platformdirs data directory.
    :returns: The resolved ``NICEGUI_STORAGE_PATH`` value.
    """
    app_name = app_name_from_argv(app_name)
    if "NICEGUI_STORAGE_PATH" not in os.environ:
        import platformdirs

        env_path = (
            Path(platformdirs.PlatformDirs(app_name).user_data_dir) / "nicegui"
        ).resolve()
        os.environ["NICEGUI_STORAGE_PATH"] = str(env_path)
        logger.debug("set default NICEGUI_STORAGE_PATH=%s", env_path)
    return os.environ["NICEGUI_STORAGE_PATH"]
