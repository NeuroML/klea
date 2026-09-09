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


async def resolve_user_id() -> str:
    """Return the persistent per-browser ``user_id``, creating it if missing.

    Must be called at the top of the page builder before any ``await``
    so ``app.storage.user`` is still in the request context.

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
