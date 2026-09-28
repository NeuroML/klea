#!/usr/bin/env python3
"""
Per-session model configuration endpoints for runtime model switching.

File: klea_utils/api/models.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail DOT com>
"""

import logging

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from klea_utils.api.sessions_db import SessionStore
from klea_utils.plogging import mask_sensitive

logger = logging.getLogger(__name__)


class ChatModelConfigPayload(BaseModel):
    model: str
    api_key: str | None = None
    base_url: str | None = None
    provider: str | None = None
    user_id: str = ""


def create_models_router() -> APIRouter:
    """Create an APIRouter for per-chat model configuration.

    ``GET /chat/{user_id}/{chat_id}/models/overrides``
        Returns stored model overrides for a chat.

    ``GET /chat/{user_id}/{chat_id}/models/active``
        Returns resolved model config (defaults merged with overrides).

    ``POST /chat/{user_id}/{chat_id}/models/overrides/{role}``
        Stores per-chat model overrides.
    """
    router = APIRouter(prefix="/chat", tags=["models"])

    @router.get("/{user_id}/{chat_id}/models/overrides")
    async def get_chat_model_overrides(user_id: str, chat_id: str, request: Request):
        store: SessionStore = request.app.state.chat_sessions
        overrides = store.get_overrides(user_id, chat_id)
        logger.debug(
            "get_chat_model_overrides(%s, %s): %d role(s)",
            user_id,
            chat_id,
            len(overrides),
        )
        # Never return a stored secret: mask any (legacy) plaintext
        # ``api_key`` before serialising.
        return mask_sensitive(overrides)

    @router.get("/{user_id}/{chat_id}/models/active")
    async def get_chat_active_models(user_id: str, chat_id: str, request: Request):
        """Return the resolved model config per role (defaults + chat overrides).

        Reads the graph's ``llm_models`` dict for defaults and merges any
        per-chat overrides on top.
        """
        # Lazy: BaseLangGraph is the base class for all graphs
        from klea_utils.graph.base import BaseLangGraph

        graph: BaseLangGraph = request.app.state.graph
        store: SessionStore = request.app.state.chat_sessions
        from typing import Any

        defaults: dict[str, dict[str, Any]] = {}
        for role, entry in graph.llm_models.items():
            cfg: dict[str, Any] = {"model": entry.model_name or ""}
            from klea_utils.llm import parse_model_name

            parsed = parse_model_name(entry.model_name)
            if parsed.provider:
                cfg["provider"] = parsed.provider
            cfg["modifiable"] = getattr(entry, "modifiable", True)
            cfg["required"] = getattr(entry, "required", True)
            defaults[role] = cfg

        overrides = store.get_overrides(user_id, chat_id)
        for role, override in overrides.items():
            if role in defaults:
                defaults[role]["model"] = override.get("model", defaults[role]["model"])
                if override.get("provider"):
                    defaults[role]["provider"] = override["provider"]
                else:
                    from klea_utils.llm import parse_model_name

                    parsed = parse_model_name(defaults[role]["model"])
                    if parsed and parsed.provider:
                        defaults[role]["provider"] = parsed.provider
                if override.get("api_key"):
                    defaults[role]["api_key"] = f"...{override['api_key'][-4:]}"
                if override.get("base_url"):
                    defaults[role]["base_url"] = override["base_url"]
            else:
                defaults[role] = override

        for role, value in defaults.items():
            value["overridden"] = role in overrides

        logger.debug(
            "get_chat_active_models(%s, %s): %d role(s)",
            user_id,
            chat_id,
            len(defaults),
        )
        return defaults

    def _is_modifiable(graph: object, role: str) -> bool:
        """Return whether a model role can be modified by the user."""
        entry = getattr(graph, "llm_models", {}).get(role)
        if entry is None:
            return True
        return getattr(entry, "modifiable", True)

    @router.post("/{user_id}/{chat_id}/models/overrides/{role}")
    async def set_chat_model_override(
        user_id: str,
        chat_id: str,
        role: str,
        payload: ChatModelConfigPayload,
        request: Request,
    ):
        # Lazy: BaseLangGraph is the base class for all graphs
        from klea_utils.graph.base import BaseLangGraph

        graph: BaseLangGraph = request.app.state.graph
        if not _is_modifiable(graph, role):
            raise HTTPException(
                status_code=403,
                detail=f"The '{role}' model is locked and cannot be modified.",
            )
        store: SessionStore = request.app.state.chat_sessions
        store.create_chat(user_id, chat_id)
        store.set_override(
            user_id,
            chat_id,
            role,
            payload.model_dump(exclude={"user_id"}, exclude_none=True),
        )
        logger.debug(
            "set_chat_model_override(%s, %s, role=%s, model=%s)",
            user_id,
            chat_id,
            role,
            payload.model,
        )
        return {
            "status": "ok",
            "chat_id": chat_id,
            "role": role,
            "model": payload.model,
        }

    @router.delete("/{user_id}/{chat_id}/models/overrides/{role}")
    async def clear_chat_model_override(
        user_id: str,
        chat_id: str,
        role: str,
        request: Request,
    ):
        """Remove the model override for a single role in a chat."""
        from klea_utils.graph.base import BaseLangGraph

        graph: BaseLangGraph = request.app.state.graph
        if not _is_modifiable(graph, role):
            raise HTTPException(
                status_code=403,
                detail=f"The '{role}' model is locked and cannot be reset.",
            )
        store: SessionStore = request.app.state.chat_sessions
        store.clear_override(user_id, chat_id, role)
        logger.debug(
            "clear_chat_model_override(%s, %s, role=%s)", user_id, chat_id, role
        )
        return {"status": "ok", "chat_id": chat_id, "role": role}

    return router
