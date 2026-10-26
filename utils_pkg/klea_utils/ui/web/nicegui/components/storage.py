#!/usr/bin/env python3
"""
NiceGUI user-storage helpers for Klea pages.

Covers the persistent per-browser identity (``app.storage.user``) and
defends against the stale-session case where NiceGUI raises
``AssertionError`` because the backing ``storage-user-*.json`` file was
lost while the browser still sends the old session cookie.

File: klea_utils/ui/web/nicegui/components/storage.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail DOT com>
"""

import logging
import os
from typing import Any

from nicegui import app, core
from nicegui.storage import request_contextvar

logger = logging.getLogger(__name__)


async def ensure_user_storage():
    """Return ``app.storage.user``, recreating it if stale.

    When the ``.nicegui/storage-user-*.json`` file is missing but the
    browser still sends the old session cookie, ``app.storage.user``
    raises ``AssertionError``. We warn and recreate the backing
    ``FilePersistentDict`` for that ``session_id`` so the page can
    continue with a fresh ``user_id`` instead of 500.
    """
    try:
        return app.storage.user
    except AssertionError as e:
        request = request_contextvar.get()
        session_id = request.session.get("id", "unknown") if request else "unknown"
        logger.warning(
            f"stale nicegui session {session_id = } missing storage, recreating: {e}"
        )
        if request is not None:
            await core.app.storage._create_user_storage(session_id)
        return app.storage.user


def user_storage_or_none():
    """Return ``app.storage.user`` or ``None`` if stale (no await)."""
    try:
        return app.storage.user
    except AssertionError as e:
        request = request_contextvar.get()
        session_id = request.session.get("id", "unknown") if request else "unknown"
        logger.warning(f"stale nicegui session {session_id = } at storage access: {e}")
        return None


def safe_set_user(key: str, value: Any) -> None:
    """Set ``app.storage.user[key]`` if storage is available, else warn."""
    store = user_storage_or_none()
    if store is not None:
        store[key] = value
    else:
        logger.warning(f"skipping persistent set {key}={value!r} due to stale storage")


async def browser_user_id() -> str:
    """Return the persistent per-browser ``user_id``, creating it if missing.

    The multi-user fallback: identity comes from the browser's signed session
    cookie (``app.storage.user``), so each browser/profile is a distinct user.
    Must be called before any ``await`` so ``app.storage.user`` is still in the
    request context.

    :returns: The stored (or freshly generated) ``user_id`` string.
    """
    store = await ensure_user_storage()
    if "user_id" not in store:
        import uuid

        store["user_id"] = str(uuid.uuid4())
        logger.debug("NEW user_id=%s", store["user_id"])
    else:
        logger.debug("EXISTING user_id=%s", store["user_id"])
    return store["user_id"]


def client_is_loopback() -> bool:
    """Return whether the current request's client is on a loopback host.

    Guards the shared local identity file: a remote browser must never be
    handed the machine-local ``user_id`` (it would expose the local user's
    chats).  A ``None`` client (no request context) is treated as not loopback.

    :returns: True when the request client host is loopback.
    """
    # Lazy: the host helper is only needed when a request context exists.
    from klea_utils.net import is_loopback_host

    request = request_contextvar.get()
    if request is None or request.client is None:
        return False
    return is_loopback_host(request.client.host or "")


async def resolve_page_user_id(
    *,
    explicit: str = "",
    server_url: str = "",
    identity_app_name: str = "klea-agent",
) -> str:
    """Resolve the page's ``user_id`` through the shared identity seam.

    Order: an explicit ``--user-id`` / ``KLEA_USER_ID`` > the canonical
    host-local identity file (only when both the backend URL and the requesting
    browser are loopback, so the TUI and web share one identity for a local
    single-user setup) > the per-browser cookie (remote / multi-user).

    :param explicit: The ``--user-id`` value, or ``""``.
    :param server_url: Base URL of the backend API server.
    :param identity_app_name: Canonical identity key (the server app, e.g.
        ``"klea-agent"``).
    :returns: The resolved user id.
    """
    # Lazy: identity pulls in platformdirs/api.server only when needed.
    from klea_utils.ui.identity import (
        USER_ID_ENV_VAR,
        is_loopback_url,
        persisted_user_id,
    )

    explicit = (explicit or "").strip() or os.environ.get(USER_ID_ENV_VAR, "").strip()
    if explicit:
        logger.debug("explicit user_id=%s", explicit)
        return explicit
    if is_loopback_url(server_url) and client_is_loopback():
        value = persisted_user_id(identity_app_name)
        logger.debug("canonical local user_id=%s (app=%s)", value, identity_app_name)
        return value
    return await browser_user_id()
