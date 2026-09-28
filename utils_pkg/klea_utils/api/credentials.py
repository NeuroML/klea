#!/usr/bin/env python3
"""
Per-user provider credential endpoints.

Credentials are stored per user, keyed by provider (plus the endpoint for
custom / explicit-URL models), and are never returned raw: the API only
reports a masked suffix and, when no credential is stored, whether the
provider's environment variable is set.

File: klea_utils/api/credentials.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail DOT com>
"""

import logging
import os
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from klea_utils.api.sessions_db import SessionStore
from klea_utils.llm import (
    credential_scope,
    provider_api_key_env,
    provider_requires_api_key,
)

logger = logging.getLogger(__name__)


def mask_secret(secret: str | None) -> str:
    """Return a masked representation of *secret* (last 4 characters)."""
    if not secret:
        return ""
    return f"...{str(secret)[-4:]}"


def credential_status(
    store: SessionStore, user_id: str, model_name: str
) -> dict[str, Any]:
    """Resolve how *model_name*'s credential is supplied.

    Checks, in order: a stored per-user credential for the model's
    provider(+endpoint), then the provider's API-key environment variable.
    Providers that need no key (e.g. Ollama) report ``source="none"`` with
    ``requires_key=False`` so callers do not treat them as unconfigured.

    :param store: The session store holding per-user credentials.
    :param user_id: Persistent user identifier.
    :param model_name: Provider-prefixed model string.
    :returns: ``{provider, endpoint, requires_key, source, masked}`` where
        *source* is ``"user"``, ``"env"`` or ``"none"``.
    """
    scope = credential_scope(model_name)
    requires = provider_requires_api_key(scope.provider)
    stored = (
        store.get_credential(user_id, scope.provider, scope.endpoint or "")
        if scope.provider
        else None
    )
    env_var = provider_api_key_env(scope.provider) if requires else None
    env_value = os.environ.get(env_var) if env_var else None

    if stored:
        source, masked = "user", mask_secret(stored)
    elif env_value:
        source, masked = "env", mask_secret(env_value)
    else:
        source, masked = "none", ""

    return {
        "provider": scope.provider,
        "endpoint": scope.endpoint,
        "requires_key": requires,
        "source": source,
        "masked": masked,
    }


class CredentialPayload(BaseModel):
    """Request body for storing a provider credential."""

    provider: str
    endpoint: str = ""
    secret: str


def create_credentials_router() -> APIRouter:
    """Create an APIRouter for per-user provider credentials.

    ``GET /credentials/{user_id}``
        List stored credentials (masked, with timestamps).
    ``PUT /credentials/{user_id}``
        Store/replace a credential (``provider``, ``endpoint``, ``secret``).
    ``DELETE /credentials/{user_id}/{provider}``
        Remove a provider's credential (optional ``endpoint`` query).
    """
    router = APIRouter(prefix="/credentials", tags=["credentials"])

    @router.get("/{user_id}")
    async def list_credentials(user_id: str, request: Request):
        store: SessionStore = request.app.state.chat_sessions
        # Triggers the opportunistic TTL sweep in the store.
        creds = store.list_credentials(user_id)
        result = [
            {
                "provider": c.get("provider", ""),
                "endpoint": c.get("endpoint", ""),
                "source": "user",
                "masked": mask_secret(c.get("secret")),
                "created_at": c.get("created_at", 0),
                "last_used_at": c.get("last_used_at", 0),
            }
            for c in creds
        ]
        logger.debug("list_credentials(%s): %d", user_id, len(result))
        return result

    @router.put("/{user_id}")
    async def set_credential(
        user_id: str, payload: CredentialPayload, request: Request
    ):
        provider = payload.provider.strip().lower()
        secret = payload.secret.strip()
        if not provider:
            raise HTTPException(status_code=422, detail="provider must not be empty")
        if not secret:
            raise HTTPException(status_code=422, detail="secret must not be empty")
        store: SessionStore = request.app.state.chat_sessions
        store.set_credential(user_id, provider, payload.endpoint, secret)
        logger.debug(
            "set_credential(%s, provider=%s, endpoint=%s)",
            user_id,
            provider,
            payload.endpoint,
        )
        return {
            "status": "ok",
            "provider": provider,
            "endpoint": payload.endpoint,
            "masked": mask_secret(secret),
        }

    @router.delete("/{user_id}/{provider}")
    async def clear_credential(
        user_id: str, provider: str, request: Request, endpoint: str = ""
    ):
        store: SessionStore = request.app.state.chat_sessions
        store.clear_credential(user_id, provider.strip().lower(), endpoint)
        logger.debug(
            "clear_credential(%s, provider=%s, endpoint=%s)",
            user_id,
            provider,
            endpoint,
        )
        return {
            "status": "ok",
            "provider": provider.strip().lower(),
            "endpoint": endpoint,
        }

    return router
