"""Persistent storage for TUI presentation settings.

Theme selection is UI presentation state that the application writes at
runtime (every time the user changes the theme), so it is kept in its own
small JSON file instead of the hand-edited, schema-validated ``AppConfig``.

Layout::

    <platformdirs user_config_dir>/klea/tui_settings.json
    {"theme": "<theme name>"}

Both functions are deliberately best-effort: a missing, empty, unreadable or
malformed file simply yields ``None`` from :func:`load_theme_preference`, and a
write failure is logged and swallowed so that persisting a preference can
never break the running application.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
from pathlib import Path

logger = logging.getLogger(__name__)

_APP_NAME = "klea"
_SETTINGS_FILENAME = "tui_settings.json"
_THEME_KEY = "theme"


def _fallback_config_dir() -> Path:
    """Return a config directory when ``platformdirs`` is unavailable."""
    base = os.environ.get("XDG_CONFIG_HOME")
    if base:
        return Path(base).expanduser() / _APP_NAME
    return Path.home() / ".config" / _APP_NAME


def _config_dir() -> Path:
    """Return the directory holding the TUI settings file."""
    try:
        from platformdirs import user_config_dir
    except Exception:  # pragma: no cover - platformdirs is expected to exist
        return _fallback_config_dir()
    try:
        return Path(user_config_dir(_APP_NAME)).expanduser()
    except Exception:  # pragma: no cover - defensive
        return _fallback_config_dir()


def settings_path() -> Path:
    """Return the path of the JSON file holding TUI settings."""
    return _config_dir() / _SETTINGS_FILENAME


def load_theme_preference() -> str | None:
    """Return the stored theme name, or ``None`` when unavailable/invalid."""
    path = settings_path()
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except FileNotFoundError:
        return None
    except (OSError, ValueError) as exc:
        logger.debug("Could not read theme preference from %s: %s", path, exc)
        return None

    if not isinstance(data, dict):
        logger.debug("Ignoring theme preference in %s: not a JSON object", path)
        return None

    theme = data.get(_THEME_KEY)
    if isinstance(theme, str) and theme.strip():
        return theme.strip()
    return None


def save_theme_preference(theme: str) -> None:
    """Persist ``theme`` as the preferred theme.

    The write is atomic (temporary file + ``os.replace``) and non-fatal: any
    error is logged and swallowed so a read-only or missing config directory
    cannot crash the TUI.
    """
    if not isinstance(theme, str) or not theme.strip():
        return

    path = settings_path()
    payload = json.dumps({_THEME_KEY: theme.strip()}, indent=2) + "\n"

    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(
            dir=str(path.parent), prefix=".tui_settings-", suffix=".tmp"
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(payload)
            os.replace(tmp_name, path)
        except BaseException:
            try:
                os.unlink(tmp_name)
            except OSError:
                pass
            raise
    except OSError as exc:
        logger.debug("Could not save theme preference to %s: %s", path, exc)
