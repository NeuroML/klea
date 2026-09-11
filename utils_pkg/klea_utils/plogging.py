#!/usr/bin/env python3
"""
Logging related utils

File: klea_utils/plogging.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail DOT com>
"""

import logging
import os
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any

import colorlog

#: Klea logger namespaces that are turned up to DEBUG by
#: ``setup_root_logger``.  Everything else (third-party libraries) inherits
#: the root logger's INFO level, so their DEBUG output is filtered at the
#: source without having to enumerate them.
KLEA_LOG_NAMESPACES = (
    "klea_utils",
    "klea_rag",
    "klea_agent",
    "neuroml_mcp",
)

logger = logging.getLogger(__name__)


class LoggerNotInfoFilter(logging.Filter):
    """Allow only non INFO messages"""

    def filter(self, record):
        return record.levelno != logging.INFO


class LoggerInfoFilter(logging.Filter):
    """Allow only INFO messages"""

    def filter(self, record):
        return record.levelno == logging.INFO


#: Environment variable that selects the console logging level across all
#: Klea CLIs (see :func:`resolve_log_level`).  Read from the process
#: environment only -- the app env files (e.g. ``klea_agent.env``) are
#: loaded after logging is configured, so this var cannot be set there.
KLEA_LOG_LEVEL_ENV = "KLEA_LOG_LEVEL"


def resolve_log_level(debug: bool = False) -> int:
    """Return the console logging level for the current process.

    Precedence, highest first:

    #. ``--debug`` (the *debug* flag) -- always ``DEBUG``
    #. the :data:`KLEA_LOG_LEVEL_ENV` environment variable -- accepted as a
       case-insensitive level name (``debug``/``info``/``warning``/``error``/
       ``critical``) or a numeric level
    #. ``INFO``

    An unknown :data:`KLEA_LOG_LEVEL_ENV` value is logged as a warning and
    falls back to ``INFO``.  Intended to be shared by every Klea CLI so the
    ``--debug`` flag and env var behave consistently across them.

    :param debug: ``True`` when the ``--debug`` flag was given
    :returns: A :mod:`logging` level constant
    """
    if debug:
        return logging.DEBUG
    raw = os.environ.get(KLEA_LOG_LEVEL_ENV)
    if not raw:
        return logging.INFO
    if raw.strip().lstrip("-").isdigit():
        level = int(raw)
    else:
        try:
            level = logging.getLevelName(raw.upper())
        except TypeError:
            level = 0
    if isinstance(level, int) and logging.DEBUG <= level <= logging.CRITICAL:
        return level
    logger.warning(f"Unknown {KLEA_LOG_LEVEL_ENV}={raw!r}; using INFO")
    return logging.INFO


def enable_debug_logging() -> None:
    """Set :data:`KLEA_LOG_LEVEL_ENV` to ``debug`` in this process.

    Called by a ``--debug`` flag on a CLI that spawns child processes (a
    client starting a server, a serve command) so the child resolves
    ``DEBUG`` via :func:`resolve_log_level` -- environment variables are
    inherited across the subprocess boundary.
    """
    os.environ[KLEA_LOG_LEVEL_ENV] = "debug"


#: Console formatters colorize each line by level (whole-line color via the
#: ``%(log_color)s`` prefix).  The file formatter stays plain so ANSI escape
#: codes never end up in log files.
logger_formatter_info = colorlog.ColoredFormatter(
    "%(log_color)s%(asctime)s %(name)s (%(levelname)s) >>> %(message)s\n\n"
)
logger_formatter_other = colorlog.ColoredFormatter(
    "%(log_color)s%(asctime)s %(name)s (%(levelname)s) in '%(funcName)s' >>> %(message)s\n\n"
)
logger_formatter_file = logging.Formatter(
    "%(asctime)s %(name)s (%(levelname)s) in '%(funcName)s' >>> %(message)s"
)


def setup_root_logger(
    app_name: str,
    stderr_level: int = logging.INFO,
    log_dir: str | Path | None = None,
) -> logging.Logger:
    """Configure the root logger once per process.

    Idempotent: if the root logger already has handlers, this is a no-op
    and the existing configuration is returned unchanged.

    Adds, on the root logger:

    * a stdout handler for INFO messages (simple format)
    * a stderr handler for all other levels at ``stderr_level`` (format
      includes the function name)
    * an optional ``RotatingFileHandler`` at ``{log_dir}/{app_name}.log``
      logging all levels at DEBUG when ``log_dir`` is provided

    The root logger is set to INFO.  The Klea logger namespaces (see
    ``KLEA_LOG_NAMESPACES``) and the application logger (``app_name``) are
    raised to DEBUG so our own logs are captured in full.  Because module
    loggers propagate to the root logger by default, a single call from each
    application entry point routes all Klea logs (library modules, graph
    nodes, API routers) through the same console and file handlers.
    Third-party libraries inherit the root's INFO level, so their DEBUG
    output is filtered at the source without enumerating them.

    The console default is ``INFO`` (progress on stdout, warnings and
    errors on stderr) -- end users see no debug noise.  Pass
    ``stderr_level=logging.DEBUG`` (e.g. from :func:`resolve_log_level`
    when ``--debug`` / ``KLEA_LOG_LEVEL`` request it) to surface full
    detail on the console.  The rotating log file always captures
    ``DEBUG`` regardless, so verbose logs remain available for
    diagnostics.

    :param app_name: Application name, used as the log file name to keep
        per-app logs separate (e.g. ``"klea-rag"``).
    :param stderr_level: Level for the stderr handler (default ``INFO``)
    :param log_dir: Directory for the log file.  ``None`` disables file
        logging.
    :returns: The configured root logger
    """
    root = logging.getLogger()
    if root.handlers:
        return root

    root.setLevel(logging.INFO)

    stdout_handler = logging.StreamHandler(sys.stdout)
    stdout_handler.setLevel(logging.INFO)
    stdout_handler.addFilter(LoggerInfoFilter())
    stdout_handler.setFormatter(logger_formatter_info)
    root.addHandler(stdout_handler)

    stderr_handler = logging.StreamHandler(sys.stderr)
    stderr_handler.setLevel(stderr_level)
    stderr_handler.addFilter(LoggerNotInfoFilter())
    stderr_handler.setFormatter(logger_formatter_other)
    root.addHandler(stderr_handler)

    if log_dir:
        log_path = Path(log_dir)
        log_path.mkdir(parents=True, exist_ok=True)
        file_handler = RotatingFileHandler(
            log_path / f"{app_name}.log",
            maxBytes=1_000_000,
            backupCount=5,
        )
        file_handler.setLevel(logging.DEBUG)
        file_handler.setFormatter(logger_formatter_file)
        root.addHandler(file_handler)

    # Turn up verbosity for our own namespaces only.  Third-party libraries
    # inherit root's INFO level, so their DEBUG is filtered without listing
    # them.  Only applied when the logger's level is still NOTSET so an
    # explicit configuration always wins.
    for name in (*KLEA_LOG_NAMESPACES, app_name):
        klea_logger = logging.getLogger(name)
        if klea_logger.level == logging.NOTSET:
            klea_logger.setLevel(logging.DEBUG)

    return root


#: Default keys whose values are masked in logs by :func:`mask_sensitive`.
#: Covers the generic ``api_key`` plus the provider-specific field names Klea
#: may copy it into (Anthropic, HuggingFace).
DEFAULT_SENSITIVE_KEYS: frozenset[str] = frozenset(
    {"api_key", "anthropic_api_key", "huggingfacehub_api_token"}
)


def mask_sensitive(
    data: dict[str, Any],
    sensitive_keys: set[str] | None = None,
) -> dict[str, Any]:
    """Return a copy with sensitive values masked for logging.

    Shows only the last 4 characters of each matching value to prevent
    secrets (API keys, tokens) from appearing in plaintext in log output.
    Recurses into nested dicts, so e.g. ``{"model_overrides":
    {"chat": {"api_key": ...}}}`` is sanitized too.

    :param data: The dict to sanitize.
    :param sensitive_keys: Keys whose values should be masked.
        Defaults to :data:`DEFAULT_SENSITIVE_KEYS`.
    :returns: New dict with masked values.
    """
    keys = sensitive_keys or set(DEFAULT_SENSITIVE_KEYS)
    safe: dict[str, Any] = {}
    for key, val in data.items():
        if isinstance(val, dict):
            safe[key] = mask_sensitive(val, sensitive_keys=keys)
        elif key in keys and val:
            safe[key] = f"...{str(val)[-4:]}"
        else:
            safe[key] = val
    return safe
