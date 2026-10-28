#!/usr/bin/env python3
"""
Framework-agnostic model-configuration helpers (shared by web and TUI).

The model-config dialog exists twice (a NiceGUI dialog and a Textual modal)
but the logic behind it does not: parsing a role's model string into editable
fields and joining them back, deciding the credential scope and status of a
(possibly unsaved) model, selecting the roles to write (every shown modifiable
one, compared against the effective model) and persisting a role's override.  This module holds that logic with no frontend imports, so
both frontends share one source of truth.  The frontends keep only their own
widgets and styling (a tone is mapped to a colour class per frontend).

File: klea_utils/ui/model_config.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail DOT com>
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from klea_utils.api.sse import fetch_catalogue_models, fetch_catalogue_providers
from klea_utils.llm import (
    HUGGINGFACE_DEFAULT_INFERENCE_PROVIDER,
    HUGGINGFACE_LOCAL_SUFFIX,
    HUGGINGFACE_PROVIDER,
    PROVIDERS_WITHOUT_CUSTOM_URL,
    ParsedModelName,
    credential_scope,
    join_model_string,
    parse_model_name,
    requires_api_key,
)
from klea_utils.ui.client import (
    clear_model_override,
    clear_session_model_override,
    set_model_override,
    set_session_model_override,
)

logger = logging.getLogger(__name__)

#: Credential-status tones, mapped to a colour/class by each frontend.
TONE_MUTED = "muted"
TONE_ERROR = "error"


def role_label(role: str) -> str:
    """Return the human-facing label for a model role key.

    ``tool_picker`` -> ``"Tool picker"`` (``str.capitalize()`` alone would
    give ``"Tool_picker"``).

    :param role: Model role key (``chat``, ``tool_picker``, ...).
    :returns: Display label.
    """
    return role.replace("_", " ").capitalize()


@dataclass(frozen=True)
class ModelOrigin:
    """Parsed components of a role's saved model string.

    Holds both the originals (for the "unchanged" branch of
    :func:`build_model_string`) and the initial field values a picker is
    prefilled with.

    :param provider: Original provider id.
    :param model: Original model id.
    :param suffix: Original non-URL suffix (put back while the model is
        unchanged); empty for a URL suffix.
    :param url: Initial custom-URL field value.
    :param backend: Initial HuggingFace backend field value.
    :param inference_provider: Initial HuggingFace inference-provider value.
    """

    provider: str
    model: str
    suffix: str
    url: str
    backend: str
    inference_provider: str


def parse_model_origin(model: str) -> ModelOrigin:
    """Parse *model* into a :class:`ModelOrigin` for prefilling a picker.

    A half-typed string (e.g. ``openai:``) is kept whole in the model field
    so nothing the user typed is lost.

    :param model: The saved model string.
    :returns: The parsed origin and initial field values.
    """
    model = (model or "").strip()
    try:
        parsed = parse_model_name(model)
    except ValueError:
        parsed = ParsedModelName(provider=None, model_name=model, suffix=None)
    suffix = parsed.suffix or ""
    is_url = suffix.startswith(("http://", "https://"))
    provider = parsed.provider or ""
    original_suffix = "" if is_url else suffix
    if provider.lower() == HUGGINGFACE_PROVIDER:
        backend = "local" if suffix == HUGGINGFACE_LOCAL_SUFFIX else "endpoint"
        inference_provider = (
            ""
            if backend == "local"
            else (suffix or HUGGINGFACE_DEFAULT_INFERENCE_PROVIDER)
        )
    else:
        backend = "endpoint"
        inference_provider = ""
    return ModelOrigin(
        provider=provider,
        model=parsed.model_name,
        suffix=original_suffix,
        url=suffix if is_url else "",
        backend=backend,
        inference_provider=inference_provider,
    )


def build_model_string(
    provider: str,
    model: str,
    *,
    url: str = "",
    backend: str = "endpoint",
    inference_provider: str = "",
    original: ModelOrigin | None = None,
) -> str:
    """Join the picker's field values back into a model string.

    HuggingFace joins from its backend + inference-provider fields; other
    providers use the custom URL when one is set, else restore the original
    suffix while the model is unchanged.

    :param provider: Provider field value.
    :param model: Model field value.
    :param url: Custom-URL field value.
    :param backend: HuggingFace backend field value.
    :param inference_provider: HuggingFace inference-provider field value.
    :param original: The role's original parsed model, if any.
    :returns: The joined model string.
    """
    provider = (provider or "").strip()
    model = (model or "").strip()
    if provider.lower() == HUGGINGFACE_PROVIDER:
        if (backend or "endpoint") == "local":
            suffix = HUGGINGFACE_LOCAL_SUFFIX
        else:
            suffix = (
                inference_provider or ""
            ).strip() or HUGGINGFACE_DEFAULT_INFERENCE_PROVIDER
        return join_model_string(provider, model, suffix)
    url = (url or "").strip()
    if url and provider.lower() not in PROVIDERS_WITHOUT_CUSTOM_URL:
        suffix = url
    elif original is not None and (provider, model) == (
        original.provider,
        original.model,
    ):
        suffix = original.suffix
    else:
        suffix = None
    return join_model_string(provider, model, suffix)


def required_credential_scopes(info: Mapping[str, Any]) -> dict[tuple[str, str], dict]:
    """Return the distinct provider(+endpoint) scopes the roles need a key for.

    :param info: The model config (``role -> cfg``).
    :returns: ``{(provider, endpoint): credential}`` for key-requiring roles.
    """
    scopes: dict[tuple[str, str], dict] = {}
    for role_cfg in info.values():
        cred = role_cfg.get("credential") or {}
        if not cred.get("requires_key"):
            continue
        provider = cred.get("provider") or ""
        if not provider:
            continue
        scopes[(provider, cred.get("endpoint") or "")] = cred
    return scopes


def live_credential(
    model_name: str,
    saved: Mapping[str, Any] | None,
    stored_by_scope: Mapping[tuple[str, str], Mapping[str, Any]],
) -> dict[str, Any]:
    """Credential block for a (possibly unsaved) model string.

    Provider and key requirement are derived client-side so typed but
    unsaved models are recognised; the source/masked come from the stored
    credentials or, when the scope matches, the saved block.

    :param model_name: The model string.
    :param saved: The saved credential block, if any.
    :param stored_by_scope: Stored credentials keyed ``(provider, endpoint)``.
    :returns: A credential block (``provider``/``endpoint``/``requires_key``/
        ``source``/``masked``).
    """
    scope = credential_scope(model_name)
    provider = scope.provider or ""
    endpoint = scope.endpoint or ""
    block: dict[str, Any] = {
        "provider": provider,
        "endpoint": endpoint,
        "requires_key": requires_api_key(model_name),
    }
    saved = saved or {}
    same_scope = (
        saved.get("provider", "") == provider
        and (saved.get("endpoint") or "") == endpoint
    )
    stored_cred = stored_by_scope.get((provider, endpoint))
    if stored_cred:
        block["source"] = "user"
        block["masked"] = stored_cred.get("masked", "")
    elif same_scope and saved.get("source") in ("env", "user"):
        block["source"] = saved.get("source")
        block["masked"] = saved.get("masked", "")
    else:
        block["source"] = "none"
        block["masked"] = ""
    return block


def credential_display(
    model_name: str,
    saved: Mapping[str, Any] | None,
    stored_by_scope: Mapping[tuple[str, str], Mapping[str, Any]],
) -> tuple[str, str]:
    """Return ``(label, tone)`` for a model's credential status.

    :param model_name: The model string.
    :param saved: The saved credential block, if any.
    :param stored_by_scope: Stored credentials keyed ``(provider, endpoint)``.
    :returns: The display label and a tone (``"muted"`` / ``"error"``).
    """
    cred = live_credential(model_name, saved, stored_by_scope)
    provider = cred["provider"]
    if not model_name:
        return "No model selected", TONE_MUTED
    if not provider:
        return "No API key configured", TONE_ERROR
    if not cred["requires_key"]:
        return "No API key required", TONE_MUTED
    if cred["source"] == "env":
        return f"Using API key from the environment ({provider})", TONE_MUTED
    if cred["source"] == "user":
        return f"Stored API key {cred.get('masked', '')} ({provider})", TONE_MUTED
    return f"No API key configured for {provider}", TONE_ERROR


def live_info(
    info: Mapping[str, Any],
    role_values: Mapping[str, str],
    stored_by_scope: Mapping[tuple[str, str], Mapping[str, Any]],
) -> dict[str, Any]:
    """Model config built from the dialog's current input values.

    :param info: The saved model config (``role -> cfg``).
    :param role_values: Current input values by role.
    :param stored_by_scope: Stored credentials keyed ``(provider, endpoint)``.
    :returns: A copy of *info* with each role's model and credential block
        replaced by its live values.
    """
    live: dict[str, Any] = {}
    for role, cfg in info.items():
        cfg = dict(cfg)
        model = role_values.get(role, cfg.get("model", ""))
        cfg["model"] = model
        cfg["credential"] = live_credential(
            model, cfg.get("credential") or {}, stored_by_scope
        )
        live[role] = cfg
    return live


def changed_roles(
    info: Mapping[str, Any], role_values: Mapping[str, str]
) -> list[tuple[str, str]]:
    """Return the ``(role, new_model)`` pairs whose value changed.

    *info* must be the *effective* (precedence-resolved) config: the baseline
    is the model each role would actually use, not the dialog's seed or a
    stored snapshot, so an untouched inherited value is not reported as a
    change and is not pinned.  Locked roles are skipped.

    :param info: The effective model config (``role -> cfg``).
    :param role_values: Current input values by role.
    :returns: The changed roles, in iteration order.
    """
    changed: list[tuple[str, str]] = []
    for role, new_model in role_values.items():
        cfg = info.get(role, {})
        if not cfg.get("modifiable", True):
            continue
        new_model = (new_model or "").strip()
        if new_model == cfg.get("model", ""):
            continue
        changed.append((role, new_model))
    return changed


def shown_overrides(
    info: Mapping[str, Any], role_values: Mapping[str, str]
) -> list[tuple[str, str, bool]]:
    """Return the roles to persist: every shown modifiable role with a model.

    Unlike :func:`changed_roles`, *info* must be the *effective* config: every
    shown modifiable role with a non-empty model is written, so "save what is
    shown" holds even when the dialog's seed was stale.  The third element
    says whether the value differs from the effective model, so that only a
    real change is promoted into the session default and an untouched role is
    not stamped over it.

    :param info: The effective model config (``role -> cfg``).
    :param role_values: Current input values by role.
    :returns: ``(role, model, changed)`` triples, in iteration order.
    """
    changed = dict(changed_roles(info, role_values))
    shown: list[tuple[str, str, bool]] = []
    for role, new_model in role_values.items():
        cfg = info.get(role, {})
        if not cfg.get("modifiable", True):
            continue
        new_model = (new_model or "").strip()
        if not new_model:
            continue
        shown.append((role, new_model, changed.get(role) == new_model))
    return shown


def use_for_all_targets(
    info: Mapping[str, Any], role: str, value: str
) -> dict[str, str]:
    """Return the roles of the same kind that *role*'s model applies to.

    Scoped by ``model_type`` so a chat model is never copied onto the
    embedding role.  Autofills only; the user still saves.

    :param info: The model config (``role -> cfg``).
    :param role: The source role.
    :param value: The model string to apply.
    :returns: ``{target_role: value}`` for the other modifiable same-kind roles.
    """
    source_type = info.get(role, {}).get("model_type", "text_generation")
    applied: dict[str, str] = {}
    for target, cfg in info.items():
        if target == role or not cfg.get("modifiable", True):
            continue
        if cfg.get("model_type", "text_generation") != source_type:
            continue
        applied[target] = value
    return applied


class CatalogueCache:
    """Lazily fetch and cache the models.dev catalogue for a user.

    Providers are fetched once; each provider's models are fetched the first
    time it is picked and cached for the life of the instance.

    :param server_url: Base URL of the backend API server.
    :param user_id: Opaque persistent user identifier.
    """

    def __init__(self, server_url: str, user_id: str) -> None:
        """Store the server/user for later fetches."""
        self._server_url = server_url
        self._user_id = user_id
        self._providers: list[str] | None = None
        self._models: dict[str, list[str]] = {}

    async def providers(self) -> list[str]:
        """Return the catalogue providers, fetching once.

        :returns: Sorted provider ids.
        """
        if self._providers is None:
            self._providers = await fetch_catalogue_providers(
                self._server_url, self._user_id
            )
        return self._providers

    def models_for(self, provider: str) -> list[str]:
        """Return the already-loaded models for *provider* (no fetch).

        :param provider: Klea provider id.
        :returns: The cached models, or ``[]``.
        """
        return self._models.get(provider, [])

    async def load_models(self, provider: str) -> list[str]:
        """Return *provider*'s models, fetching and caching on first use.

        :param provider: Klea provider id.
        :returns: The provider's models, or ``[]``.
        """
        if provider not in self._models:
            self._models[provider] = await fetch_catalogue_models(
                self._server_url, self._user_id, provider
            )
        return self._models[provider]


@dataclass(frozen=True)
class SaveOutcome:
    """The result of persisting a dialog's shown modifiable roles.

    :param ok: Whether every shown modifiable role saved.
    :param saved: The ``(role, model)`` pairs persisted before any failure.
    :param failed_role: The role that failed, when ``ok`` is ``False``.
    """

    ok: bool
    saved: list[tuple[str, str]]
    failed_role: str = ""


async def save_roles(
    server_url: str,
    user_id: str,
    chat_id: str,
    *,
    session_scope: bool,
    info: Mapping[str, Any],
    role_values: Mapping[str, str],
) -> SaveOutcome:
    """Persist every shown modifiable role, stopping at the first failure.

    Which roles are written is decided by :func:`shown_overrides`: every shown
    modifiable role with a non-empty model is written, compared against the
    *effective* (precedence-resolved) model in *info*; only the roles whose
    model genuinely differs from that baseline are promoted into the session
    default, so an untouched inherited role is written but not stamped over
    the default.

    :param server_url: Base URL of the backend API server.
    :param user_id: Opaque persistent user identifier.
    :param chat_id: Active chat id (ignored for a session-scope save).
    :param session_scope: Save per-session defaults instead of per-chat.
    :param info: The effective model config (``role -> cfg``).
    :param role_values: Current input values by role.
    :returns: A :class:`SaveOutcome`.
    """
    saved: list[tuple[str, str]] = []
    for role, new_model, changed in shown_overrides(info, role_values):
        logger.debug(f"model config: saving {role = }\n{new_model = }\n{changed = }")
        if session_scope:
            ok = await set_session_model_override(
                server_url, user_id, role, {"model": new_model}
            )
        else:
            ok = await set_model_override(
                server_url,
                user_id,
                chat_id,
                role,
                {"model": new_model, "promote_default": changed},
            )
        if not ok:
            return SaveOutcome(ok=False, saved=saved, failed_role=role)
        saved.append((role, new_model))
    return SaveOutcome(ok=True, saved=saved)


async def clear_role(
    server_url: str,
    user_id: str,
    chat_id: str,
    *,
    session_scope: bool,
    role: str,
) -> bool:
    """Clear one role's override (reverting it to the inherited model).

    :param server_url: Base URL of the backend API server.
    :param user_id: Opaque persistent user identifier.
    :param chat_id: Active chat id (ignored for a session-scope clear).
    :param session_scope: Clear the per-session default instead of per-chat.
    :param role: The role to reset.
    :returns: Whether the reset succeeded.
    """
    if session_scope:
        return await clear_session_model_override(server_url, user_id, role)
    return await clear_model_override(server_url, user_id, chat_id, role)
