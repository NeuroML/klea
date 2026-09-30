#!/usr/bin/env python3
"""
Per-session model configuration endpoints for runtime model switching.

Model selection has three layers, lowest priority first:

1. graph/env defaults (``graph.llm_models``)
2. per-session default overrides (the user's "last used" models)
3. per-chat overrides (the distinguishing per-conversation feature)

Overrides store only the model string; API credentials live in the
provider-scoped credential store (``/credentials``) and are resolved
separately.  Any legacy plaintext ``api_key`` still present in stored
overrides is masked before it is returned.

File: klea_utils/api/models.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail DOT com>
"""

import asyncio
import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from klea_utils.api.credentials import credential_status, mask_secret
from klea_utils.api.sessions_db import SessionStore
from klea_utils.llm import parse_model_name
from klea_utils.models_catalog import list_catalog_models, list_catalog_providers
from klea_utils.plogging import mask_sensitive

logger = logging.getLogger(__name__)


class ModelPayload(BaseModel):
    """Request body for setting a role's model.

    Only the model string is persisted; credentials are managed through
    ``/credentials``.  ``promote_default`` (per-chat writes) also updates
    the per-session default so a new chat inherits the last-used model.
    Unknown fields (e.g. a legacy ``api_key``) are ignored.
    """

    model: str
    promote_default: bool = True


def _role_is_modifiable(graph: object, role: str) -> bool:
    """Return whether a model role can be modified by the user."""
    entry = getattr(graph, "llm_models", {}).get(role)
    if entry is None:
        return True
    return getattr(entry, "modifiable", True)


def _role_config(
    role: str,
    role_entry: Any,
    session_override: dict[str, Any],
    chat_override: dict[str, Any],
) -> dict[str, Any]:
    """Resolve one role's effective config (graph default < session < chat)."""
    model_name = getattr(role_entry, "model_name", "") if role_entry else ""
    if session_override.get("model"):
        model_name = session_override["model"]
    if chat_override.get("model"):
        model_name = chat_override["model"]

    role_config: dict[str, Any] = {
        "model": model_name,
        "modifiable": getattr(role_entry, "modifiable", True) if role_entry else True,
        "required": getattr(role_entry, "required", True) if role_entry else True,
    }
    parsed = parse_model_name(model_name) if model_name else None
    if parsed and parsed.provider:
        role_config["provider"] = parsed.provider
    # Carry any legacy base_url/api_key (masked) present in the overrides.
    for override in (session_override, chat_override):
        if override.get("base_url"):
            role_config["base_url"] = override["base_url"]
        if override.get("api_key"):
            role_config["api_key"] = mask_secret(override["api_key"])
    logger.debug(f"{role = }\n{role_config = }")
    return role_config


def _resolve_active(
    graph: Any,
    store: SessionStore,
    user_id: str,
    chat_id: str | None = None,
) -> dict[str, dict[str, Any]]:
    """Resolve the effective per-role config for a user (and optional chat).

    :param graph: The graph, whose ``llm_models`` are the env/config defaults.
    :param store: Session store with the override layers.
    :param user_id: Persistent user identifier.
    :param chat_id: Chat to include per-chat overrides for, or ``None`` for
        the per-session defaults view.
    :returns: ``{role: config}`` with ``overridden`` (this scope) and,
        for a chat, ``session_overridden``, plus a ``credential`` block.
    """
    session_overrides = store.get_session_overrides(user_id)
    chat_overrides = store.get_overrides(user_id, chat_id) if chat_id else {}
    llm_models = getattr(graph, "llm_models", {})

    roles: list[str] = list(llm_models)
    for role in (*session_overrides, *chat_overrides):
        if role not in roles:
            roles.append(role)
    logger.debug(
        f"{user_id = }\n{chat_id = }\n"
        f"{list(session_overrides) = }\n{list(chat_overrides) = }\n{roles = }"
    )

    resolved: dict[str, dict[str, Any]] = {}
    for role in roles:
        role_entry = llm_models.get(role)
        session_override = session_overrides.get(role) or {}
        chat_override = chat_overrides.get(role) or {}
        role_config = _role_config(role, role_entry, session_override, chat_override)
        role_config["overridden"] = (
            bool(chat_override) if chat_id else bool(session_override)
        )
        if chat_id:
            role_config["session_overridden"] = bool(session_override)
        role_config["credential"] = credential_status(
            store, user_id, role_config["model"]
        )
        resolved[role] = role_config
    return resolved


def create_models_router() -> APIRouter:
    """Create an APIRouter for per-session and per-chat model configuration.

    Model catalogue (suggestions for the model selection UIs)::

        GET    /chat/{user_id}/models/catalogue
        GET    /chat/{user_id}/models/catalogue?provider=...

    Per-session defaults::

        GET    /chat/{user_id}/models/overrides
        GET    /chat/{user_id}/models/active
        POST   /chat/{user_id}/models/overrides/{role}
        DELETE /chat/{user_id}/models/overrides/{role}

    Per-chat overrides::

        GET    /chat/{user_id}/{chat_id}/models/overrides
        GET    /chat/{user_id}/{chat_id}/models/active
        POST   /chat/{user_id}/{chat_id}/models/overrides/{role}
        DELETE /chat/{user_id}/{chat_id}/models/overrides/{role}

    A per-chat POST also promotes the model into the session default
    unless ``promote_default`` is false (the "last used" behaviour).
    """
    router = APIRouter(prefix="/chat", tags=["models"])

    def _graph_and_store(request: Request) -> tuple[Any, SessionStore]:
        # Lazy: BaseLangGraph is the base class for all graphs.
        graph: Any = request.app.state.graph
        store: SessionStore = request.app.state.chat_sessions
        return graph, store

    # ------------------------------------------------------------------
    # Model catalogue
    # ------------------------------------------------------------------

    @router.get("/{user_id}/models/catalogue")
    async def get_model_catalogue(user_id: str, provider: str | None = None):
        """Return the providers, or one provider's models, for model selection.

        Without ``provider`` the provider list is returned (models.dev
        providers plus ollama and custom); with it, that provider's model
        ids.  The first call may have to download the models.dev catalog,
        which blocks, so the lookup runs in a worker thread.
        """
        if provider:
            models = await asyncio.to_thread(list_catalog_models, provider)
            logger.debug(
                "get_model_catalogue(%s, provider=%s): %d model(s)",
                user_id,
                provider,
                len(models),
            )
            return {"provider": provider, "models": models}
        providers = await asyncio.to_thread(list_catalog_providers)
        logger.debug("get_model_catalogue(%s): %d provider(s)", user_id, len(providers))
        return {"providers": providers}

    # ------------------------------------------------------------------
    # Per-session defaults
    # ------------------------------------------------------------------

    @router.get("/{user_id}/models/overrides")
    async def get_session_model_overrides(user_id: str, request: Request):
        """Return the session default model overrides (legacy keys masked)."""
        _, store = _graph_and_store(request)
        return mask_sensitive(store.get_session_overrides(user_id))

    @router.get("/{user_id}/models/active")
    async def get_session_active_models(user_id: str, request: Request):
        """Return defaults + per-session overrides, plus credential status."""
        graph, store = _graph_and_store(request)
        data = _resolve_active(graph, store, user_id, chat_id=None)
        logger.debug("get_session_active_models(%s): %d role(s)", user_id, len(data))
        return data

    @router.post("/{user_id}/models/overrides/{role}")
    async def set_session_model_override(
        user_id: str, role: str, payload: ModelPayload, request: Request
    ):
        """Set the session default model for a role."""
        logger.debug(f"{user_id = }\n{role = }\n{payload.model = }")
        graph, store = _graph_and_store(request)
        if not _role_is_modifiable(graph, role):
            logger.warning(
                "Rejected model update for locked role=%s user=%s", role, user_id
            )
            raise HTTPException(
                status_code=403,
                detail=f"The '{role}' model is locked and cannot be modified.",
            )
        store.set_session_override(user_id, role, {"model": payload.model})
        logger.debug(
            "set_session_model_override(%s, role=%s, model=%s)",
            user_id,
            role,
            payload.model,
        )
        return {"status": "ok", "role": role, "model": payload.model}

    @router.delete("/{user_id}/models/overrides/{role}")
    async def clear_session_model_override(user_id: str, role: str, request: Request):
        """Clear the session default model override for a role."""
        logger.debug(f"{user_id = }\n{role = }")
        graph, store = _graph_and_store(request)
        if not _role_is_modifiable(graph, role):
            logger.warning(
                "Rejected model reset for locked role=%s user=%s", role, user_id
            )
            raise HTTPException(
                status_code=403,
                detail=f"The '{role}' model is locked and cannot be reset.",
            )
        store.clear_session_override(user_id, role)
        logger.debug("clear_session_model_override(%s, role=%s)", user_id, role)
        return {"status": "ok", "role": role}

    # ------------------------------------------------------------------
    # Per-chat overrides
    # ------------------------------------------------------------------

    @router.get("/{user_id}/{chat_id}/models/overrides")
    async def get_chat_model_overrides(user_id: str, chat_id: str, request: Request):
        """Return a chat's model overrides (legacy keys masked)."""
        _, store = _graph_and_store(request)
        overrides = store.get_overrides(user_id, chat_id)
        logger.debug(
            "get_chat_model_overrides(%s, %s): %d role(s)",
            user_id,
            chat_id,
            len(overrides),
        )
        return mask_sensitive(overrides)

    @router.get("/{user_id}/{chat_id}/models/active")
    async def get_chat_active_models(user_id: str, chat_id: str, request: Request):
        """Return defaults + user + chat overrides, plus credential status."""
        graph, store = _graph_and_store(request)
        data = _resolve_active(graph, store, user_id, chat_id=chat_id)
        logger.debug(
            "get_chat_active_models(%s, %s): %d role(s)",
            user_id,
            chat_id,
            len(data),
        )
        return data

    @router.post("/{user_id}/{chat_id}/models/overrides/{role}")
    async def set_chat_model_override(
        user_id: str,
        chat_id: str,
        role: str,
        payload: ModelPayload,
        request: Request,
    ):
        """Set a chat's model override, promoting it to the session default."""
        logger.debug(
            f"{user_id = }\n{chat_id = }\n{role = }\n"
            f"{payload.model = }\n{payload.promote_default = }"
        )
        graph, store = _graph_and_store(request)
        if not _role_is_modifiable(graph, role):
            logger.warning(
                "Rejected model update for locked role=%s user=%s chat=%s",
                role,
                user_id,
                chat_id,
            )
            raise HTTPException(
                status_code=403,
                detail=f"The '{role}' model is locked and cannot be modified.",
            )
        store.create_chat(user_id, chat_id)
        store.set_override(user_id, chat_id, role, {"model": payload.model})
        if payload.promote_default:
            store.set_session_override(user_id, role, {"model": payload.model})
        logger.debug(
            "set_chat_model_override(%s, %s, role=%s, model=%s, promote=%s)",
            user_id,
            chat_id,
            role,
            payload.model,
            payload.promote_default,
        )
        return {
            "status": "ok",
            "chat_id": chat_id,
            "role": role,
            "model": payload.model,
            "promoted": payload.promote_default,
        }

    @router.delete("/{user_id}/{chat_id}/models/overrides/{role}")
    async def clear_chat_model_override(
        user_id: str, chat_id: str, role: str, request: Request
    ):
        """Clear a chat's model override (falls back to the session default)."""
        logger.debug(f"{user_id = }\n{chat_id = }\n{role = }")
        graph, store = _graph_and_store(request)
        if not _role_is_modifiable(graph, role):
            logger.warning(
                "Rejected model reset for locked role=%s user=%s chat=%s",
                role,
                user_id,
                chat_id,
            )
            raise HTTPException(
                status_code=403,
                detail=f"The '{role}' model is locked and cannot be reset.",
            )
        store.clear_override(user_id, chat_id, role)
        logger.debug(
            "clear_chat_model_override(%s, %s, role=%s)", user_id, chat_id, role
        )
        return {"status": "ok", "chat_id": chat_id, "role": role}

    return router
