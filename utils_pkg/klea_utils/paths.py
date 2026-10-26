#!/usr/bin/env python3
"""
Shared user-level directory utilities for Klea packages.

Wraps ``platformdirs.PlatformDirs`` to provide OS-appropriate paths for
cache, data, and config directories (``~/.cache/klea/``, ``~/.local/share/klea/``,
``~/.config/klea/`` on Linux, with equivalents on macOS and Windows).

Consumers pass an *app_name* so that different packages (``klea``, ``nml_mcp``)
get isolated directories without repeating the boilerplate.

File: klea_utils/paths.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail DOT com>
"""

import logging
import os
from collections.abc import Mapping
from pathlib import Path

from platformdirs import PlatformDirs

logger = logging.getLogger(__name__)


def get_cache_dir(dirs: PlatformDirs) -> Path:
    """Return the OS-appropriate per-user cache directory for *dirs*.

    On Linux: ``~/.cache/{app_name}/``
    On macOS: ``~/Library/Caches/{app_name}/``
    On Windows: ``C:\\Users\\<user>\\AppData\\Local\\{app_name}\\cache\\``
    """
    return Path(dirs.user_cache_dir)


def get_data_dir(dirs: PlatformDirs) -> Path:
    """Return the OS-appropriate per-user data directory for *dirs*.

    On Linux: ``~/.local/share/{app_name}/``
    On macOS: ``~/Library/Application Support/{app_name}/``
    On Windows: ``C:\\Users\\<user>\\AppData\\Local\\{app_name}\\``
    """
    return Path(dirs.user_data_dir)


def get_config_dir(dirs: PlatformDirs) -> Path:
    """Return the OS-appropriate per-user config directory for *dirs*.

    On Linux: ``~/.config/{app_name}/``
    On macOS: ``~/Library/Preferences/{app_name}/``
    On Windows: ``C:\\Users\\<user>\\AppData\\Roaming\\{app_name}\\``
    """
    return Path(dirs.user_config_dir)


def resolve_app_config_path(
    config_file: str,
    conf_dir: str | Path,
    cwd: str | Path | None = None,
) -> Path:
    """Locate the application config file, checking the working directory first.

    ``config_file`` may be an absolute path or a relative name/path.  The
    working directory is searched before *conf_dir* (the per-app config
    directory), so a profile dropped in ``CWD`` overrides one installed in
    the config directory.

    :param config_file: App config file path or bare filename
    :param conf_dir: Config directory searched after the working directory
    :param cwd: Working directory to search first (defaults to ``Path.cwd()``)
    :returns: The first existing match
    :raises FileNotFoundError: If no existing file matches
    :raises ValueError: If *config_file* is empty
    """
    if not config_file:
        raise ValueError("Empty config file name")

    cwd_path = Path(cwd) if cwd is not None else Path.cwd()
    conf_dir_path = Path(conf_dir)

    candidate = Path(config_file)
    if candidate.is_absolute():
        if candidate.exists():
            return candidate
        raise FileNotFoundError(f"Could not find config file: {candidate}")

    cwd_candidate = cwd_path / candidate
    if cwd_candidate.exists():
        return cwd_candidate

    conf_candidate = conf_dir_path / candidate
    if conf_candidate.exists():
        return conf_candidate

    raise FileNotFoundError(
        f"Could not find config file '{config_file}' in:\n"
        f"  {cwd_path}\n"
        f"  {conf_dir_path}\n"
        "Create it there, or select another config with --profile <name>."
    )


def init_dir(path: str | Path) -> Path:
    """Create *path* (and parents) if it doesn't exist.

    :returns: The same *path* as a ``Path`` for chaining.
    """
    p = Path(path)
    p.mkdir(parents=True, exist_ok=True)
    return p


#: Application name used to derive the default per-user data directory.  The
#: ``klea_agent`` data directory is the single *storage root* that owns the
#: checkpoint database, the session database, the backend PID lockfile, the
#: endpoint file and the logs (ADR-0023, amendment "one backend per storage
#: root").
STORAGE_APP_NAME = "klea_agent"

#: Environment variable that relocates the whole storage root -- data *and*
#: runtime artifacts -- so a user can run deliberately independent instances.
#: It is an *isolation* override, not a concurrency mechanism (ADR-0023).
STORAGE_ROOT_ENV = "KLEA_AGENT_HOME"

#: Name of the SQLite database that holds LangGraph checkpoints.
CHECKPOINT_DB_NAME = "checkpoints.db"

#: Name of the SQLite database that holds chat sessions and messages.
SESSION_DB_NAME = "sessions.db"

#: Name of the PID lockfile that enforces one backend per storage root.
LOCKFILE_NAME = "klea_agent.lock"

#: Name of the file a running backend writes its endpoint (host:port) to.
ENDPOINT_FILE_NAME = "klea_agent.endpoint"

#: Subdirectory of the storage root that holds backend logs.
LOG_DIR_NAME = "logs"


def resolve_storage_root(
    data_dir: str | Path | None = None,
    env: Mapping[str, str] | None = None,
    *,
    default_root: str | Path | None = None,
    env_name: str | None = STORAGE_ROOT_ENV,
) -> Path:
    """Return the effective storage root for a Klea backend.

    Precedence, highest first:

    #. *data_dir* -- the ``--data-dir`` flag, i.e. an explicit choice
    #. *env_name* (default :data:`STORAGE_ROOT_ENV`, ``KLEA_AGENT_HOME``) --
       an opt-in relocation of the data *and* runtime artifacts
    #. *default_root* -- the graph's existing per-app data directory, when
       given
    #. the platform user data directory for :data:`STORAGE_APP_NAME`
       (``~/.local/share/klea_agent`` on Linux)

    The resolved root is logged at ``INFO`` level together with the source
    that selected it, so a user can tell which home a running backend uses.

    :param data_dir: Value of the ``--data-dir`` flag, if given
    :param env: Environment mapping to read (defaults to :data:`os.environ`)
    :param default_root: Per-app data directory used when no relocation is
        configured; preserves a graph's existing default layout
    :param env_name: Environment variable to read, or ``None`` to ignore it
    :returns: The resolved storage root, absolute and expanded
    """
    environ = os.environ if env is None else env
    if data_dir:
        root = Path(data_dir).expanduser()
        source = "the --data-dir flag"
    else:
        from_env = environ.get(env_name) if env_name else None
        if from_env:
            root = Path(from_env).expanduser()
            source = f"{env_name}={from_env!r}"
        elif default_root is not None:
            root = Path(default_root).expanduser()
            source = "the default user data dir"
        else:
            root = get_data_dir(PlatformDirs(STORAGE_APP_NAME))
            source = f"the default user data dir for {STORAGE_APP_NAME!r}"

    root = root.resolve()
    logger.info(f"Resolved storage root: {root} (from {source})")
    return root


def _storage_root(root: str | Path | None) -> Path:
    """Return *root* as a :class:`Path`, resolving it when not supplied."""
    if root is None:
        return resolve_storage_root()
    return Path(root).expanduser()


def checkpoint_db_path(root: str | Path | None = None) -> Path:
    """Return the LangGraph checkpoint database path inside *root*.

    :param root: Storage root; resolved via :func:`resolve_storage_root` when
        ``None``
    """
    return _storage_root(root) / CHECKPOINT_DB_NAME


def session_db_path(root: str | Path | None = None) -> Path:
    """Return the chat session/message database path inside *root*.

    :param root: Storage root; resolved via :func:`resolve_storage_root` when
        ``None``
    """
    return _storage_root(root) / SESSION_DB_NAME


def lockfile_path(root: str | Path | None = None) -> Path:
    """Return the backend PID lockfile path inside *root*.

    :param root: Storage root; resolved via :func:`resolve_storage_root` when
        ``None``
    """
    return _storage_root(root) / LOCKFILE_NAME


def endpoint_file_path(root: str | Path | None = None) -> Path:
    """Return the path of the file that advertises a backend's endpoint.

    :param root: Storage root; resolved via :func:`resolve_storage_root` when
        ``None``
    """
    return _storage_root(root) / ENDPOINT_FILE_NAME


def log_dir_path(root: str | Path | None = None) -> Path:
    """Return the log directory inside *root*.

    The directory is only *named*, never created: callers use
    :func:`init_dir` when they are ready to write logs.

    :param root: Storage root; resolved via :func:`resolve_storage_root` when
        ``None``
    """
    return _storage_root(root) / LOG_DIR_NAME


def cleanup_dir(path: str | Path) -> None:
    """Remove all *contents* of *path* but keep the directory itself."""
    p = Path(path)
    if not p.exists():
        return
    import shutil

    for item in p.iterdir():
        if item.is_file() or item.is_symlink():
            item.unlink()
        elif item.is_dir():
            shutil.rmtree(item)
