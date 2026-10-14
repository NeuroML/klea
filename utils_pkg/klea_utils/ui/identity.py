#!/usr/bin/env python3
"""
Frontend identity resolution (ADR-0050 invariant I5).

Frontends (the Textual TUI and the NiceGUI web UI) resolve their ``user_id``
through one seam: an explicit ``--user-id`` / ``KLEA_USER_ID``, then a
persisted (machine-local) UUID.  The persisted UUID is only sent to a loopback
server, so it is never leaked to a remote deployment; a remote server without
an explicit identity gets an empty ``user_id`` (the server treats ``user_id``
as an opaque token).

File: klea_utils/ui/identity.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail dot com>
"""

from __future__ import annotations

import logging
import os
import uuid
from pathlib import Path

logger = logging.getLogger(__name__)

#: Env var carrying an explicit user id (mirrors the ``--user-id`` flag).
USER_ID_ENV_VAR = "KLEA_USER_ID"

#: File name, under the app's user data dir, holding the persisted local UUID.
_IDENTITY_FILENAME = "user_id"


def is_loopback_url(server_url: str) -> bool:
    """Return whether *server_url* points at a loopback host.

    :param server_url: Base URL of the backend API server.
    :returns: True for ``127.0.0.1`` / ``localhost`` / ``::1``.
    """
    # Lazy: the URL helpers pull in the api.server module.
    from klea_utils.api.server import is_loopback_host, split_server_url

    host, _ = split_server_url(server_url)
    return is_loopback_host(host)


def persisted_user_id(app_name: str) -> str:
    """Return the persisted frontend-local UUID for *app_name*, creating it once.

    Stored as plain text under the app's platformdirs user data dir, so the
    same machine keeps one identity across restarts.

    :param app_name: Identity key used to locate the persisted UUID (the
        server app, e.g. ``"klea-agent"``).
    :returns: The persisted UUID.
    """
    # Lazy: platformdirs is only needed when an identity must be persisted.
    from platformdirs import PlatformDirs

    path = Path(PlatformDirs(app_name).user_data_dir) / _IDENTITY_FILENAME
    try:
        existing = path.read_text(encoding="utf-8").strip()
        if existing:
            return existing
    except OSError:
        pass
    value = str(uuid.uuid4())
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(value, encoding="utf-8")
        logger.debug("persisted user id to %s", path)
    except OSError as e:
        logger.warning("Could not persist user id: %s", e)
    return value


def resolve_user_id(
    *,
    explicit: str = "",
    env: str | None = None,
    server_url: str = "",
    app_name: str = "klea",
) -> str:
    """Resolve a frontend's ``user_id`` through the ADR-0050 seam.

    Order: *explicit* (``--user-id``) > *env* (``KLEA_USER_ID``) > the
    persisted local UUID, the last only when *server_url* is loopback (I5).

    :param explicit: The ``--user-id`` value, or ``""``.
    :param env: The env-var value; ``None`` reads ``KLEA_USER_ID``.
    :param server_url: Base URL of the backend API server.
    :param app_name: Identity key used to locate the persisted UUID (the
        server app, e.g. ``"klea-agent"``).
    :returns: The resolved user id (``""`` when none applies).
    """
    explicit = (explicit or "").strip()
    if explicit:
        return explicit
    if env is None:
        env = os.environ.get(USER_ID_ENV_VAR, "")
    env = (env or "").strip()
    if env:
        return env
    if not is_loopback_url(server_url):
        logger.debug("remote server without an explicit user id; sending none")
        return ""
    return persisted_user_id(app_name)
