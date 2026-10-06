#!/usr/bin/env python3
"""
Per-run model override and credential resolution.

Assembles the ``model_overrides`` slice the endpoint runners pass to the
graph as per-run runtime context (ADR-0033), and migrates legacy inline
plaintext keys into the credential store.  Kept separate from the endpoint
runners (:mod:`klea_utils.api.chat_core`) because the concern is
credential/override management, not request handling.

File: klea_utils/api/overrides.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail dot com>
"""

import logging
from collections.abc import Mapping
from typing import Any

from klea_utils.api.sessions_db import SessionStore
from klea_utils.llm import credential_scope

logger = logging.getLogger(__name__)


def resolve_model_overrides(
    graph: Any, store: SessionStore, user_id: str, chat_id: str
) -> dict[str, dict[str, Any]]:
    """Assemble the per-run ``model_overrides`` slice.

    Merges the per-session default overrides with the per-chat overrides
    (role-level: a chat's role dict wins entirely), then injects the
    user's provider credential (looked up by the model's provider/endpoint
    scope) as ``api_key`` for every modifiable role with a resolved model.
    Locked roles are left to the graph/env so admin-managed models are not
    affected by per-user credentials.

    :param graph: The graph, whose ``llm_models`` supply the role set and
        locked (``modifiable=False``) flags, plus env-default models.
    :param store: Session store with the override and credential layers.
    :param user_id: Persistent user identifier.
    :param chat_id: Chat conversation identifier.
    :returns: The effective ``{role: config}`` for the runtime context.
    """
    session_overrides = store.get_session_overrides(user_id)
    chat_overrides = store.get_overrides(user_id, chat_id)

    # Role-level merge: a chat's role config replaces the session one.
    resolved: dict[str, dict[str, Any]] = {}
    for role in (*session_overrides, *chat_overrides):
        if role in chat_overrides:
            resolved[role] = dict(chat_overrides[role])
        elif role in session_overrides:
            resolved[role] = dict(session_overrides[role])

    llm_models = getattr(graph, "llm_models", None)
    if not isinstance(llm_models, Mapping):
        # Tests / graphs without a role table: no metadata to inject against.
        llm_models = {}

    for role in dict.fromkeys([*resolved, *llm_models]):
        role_entry = llm_models.get(role)
        if role_entry is not None and not getattr(role_entry, "modifiable", True):
            logger.debug(f"Skipping credential injection for locked {role = }")
            continue
        role_config = resolved.get(role)
        model_name = (role_config or {}).get("model") or (
            getattr(role_entry, "model_name", "") if role_entry else ""
        )
        if not model_name:
            continue
        scope = credential_scope(model_name)
        if not scope.provider:
            continue
        secret = store.get_credential(user_id, scope.provider, scope.endpoint or "")
        if not secret:
            continue
        if role_config is None:
            role_config = {}
            resolved[role] = role_config
        # Do not clobber a legacy inline key; the startup migration should
        # have moved it, but be conservative.
        role_config.setdefault("api_key", secret)
        store.touch_credential(user_id, scope.provider, scope.endpoint or "")
        logger.debug(f"Injected credential for {role = }\n{scope = }")

    return resolved


def migrate_legacy_overrides(store: SessionStore) -> int:
    """Move legacy per-chat plaintext ``api_key`` values into credentials.

    Pre-credential deployments stored the key inline in a chat's role
    override.  Move each into ``user_credentials`` (scoped to the model's
    provider) and strip it from the override, so the unused-key TTL
    applies and override payloads no longer carry secrets.  Returns the
    number of roles migrated.
    """
    migrated = 0
    for row in store.all_chat_overrides():
        user_id = row["user_id"]
        chat_id = row["chat_id"]
        for role, role_config in row["overrides"].items():
            if not isinstance(role_config, dict) or "api_key" not in role_config:
                continue
            secret = role_config.get("api_key")
            stripped = {
                key: value for key, value in role_config.items() if key != "api_key"
            }
            scope = credential_scope(role_config.get("model", ""))
            if scope.provider and secret:
                existing = store.get_credential(
                    user_id, scope.provider, scope.endpoint or ""
                )
                if existing is None:
                    store.set_credential(
                        user_id, scope.provider, scope.endpoint or "", secret
                    )
            if stripped:
                store.set_override(user_id, chat_id, role, stripped)
            else:
                store.clear_override(user_id, chat_id, role)
            migrated += 1
            logger.info(
                "Migrated legacy api_key: user=%s chat=%s role=%s",
                user_id,
                chat_id,
                role,
            )
    if migrated:
        logger.info("Migrate legacy overrides: %d role(s) migrated", migrated)
    return migrated
