# Copyright 2026 Ankur Sinha <sanjay DOT ankur AT gmail DOT com>
"""Deterministic run identity derivation for the evaluation harness.

A run is identified by the triple ``(run_id, user_id, chat_id)`` (ADR-0057).
Every identifier is derived from the case inputs, never generated randomly, so
the same ``case_id`` and ``harness_seed`` always produce the same identifiers
and re-runs are comparable and resumable (decision D6, ADR-0057, ADR-0060).

Derivation rules:

- ``run_id`` is a UUID v5 over ``RUN_NAMESPACE`` and the name
  ``"<case_id>:<harness_seed>"``, suffixed with ``"|<config>"`` when a
  result-affecting configuration (model overrides, resolved models, endpoint)
  is supplied, where ``<config>`` is its ``sort_keys`` JSON serialisation.
- ``user_id`` is a UUID v5 over ``USER_NAMESPACE`` and the same name, so it is
  deterministic in the same inputs while staying independent of ``run_id``.
- ``chat_id`` is a UUID v5 over ``CHAT_NAMESPACE`` and the name
  ``"<run_id>:<user_id>"``, matching ADR-0057 and ADR-0060.

The namespace constants are fixed :class:`uuid.UUID` literals and must never
be changed: doing so would silently change every derived identifier. Nothing
here is random (``uuid4`` is never used) and only the standard library is
imported, so the module stays cheap to load.
"""

from __future__ import annotations

import json
import logging
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)

__all__ = [
    "CHAT_NAMESPACE",
    "RUN_NAMESPACE",
    "USER_NAMESPACE",
    "Identity",
    "derive_chat_id",
    "derive_identity",
    "derive_run_id",
    "derive_user_id",
]

#: Namespace for UUID v5 ``run_id`` derivation (ADR-0057).
RUN_NAMESPACE = uuid.UUID("5d2b1c9e-6a41-4f6b-8a0d-2e7c9f1b3a51")

#: Namespace for UUID v5 ``user_id`` derivation.
USER_NAMESPACE = uuid.UUID("9c4e7a12-3b58-4d0f-9e61-7a2d5c8b4f63")

#: Namespace for UUID v5 ``chat_id`` derivation (ADR-0057, ADR-0060).
CHAT_NAMESPACE = uuid.UUID("c1a6f3d2-8e74-4b09-a5d3-1f6b2e9c7a84")


def _seed_text(harness_seed: str | int) -> str:
    """Normalise ``harness_seed`` to the text used in the UUID v5 name."""
    text = str(harness_seed).strip()
    if not text:
        raise ValueError("harness_seed must be a non-empty string or integer")
    logger.debug("identity: %s %s", f"{text = }", f"{harness_seed = }")
    return text


def _clean_mapping(value: Any) -> dict[str, str]:
    """Return ``value`` as a stripped ``str -> str`` mapping, dropping empties."""
    if not isinstance(value, Mapping):
        return {}
    cleaned: dict[str, str] = {}
    for key, item in value.items():
        key_text = str(key).strip()
        if not key_text or item is None:
            continue
        cleaned[key_text] = str(item).strip()
    return cleaned


def _config_text(
    model_overrides: Any = None,
    resolved_models: Any = None,
    endpoint: str | None = None,
) -> str:
    """Serialise the result-affecting configuration deterministically.

    The payload is dumped with ``sort_keys`` so the same configuration always
    yields the same text.  An empty configuration yields the empty string and
    leaves the plain ``"<case_id>:<harness_seed>"`` name unchanged.
    """
    payload: dict[str, Any] = {}
    overrides = _clean_mapping(model_overrides)
    if overrides:
        payload["model_overrides"] = overrides
    resolved = _clean_mapping(resolved_models)
    if resolved:
        payload["resolved_models"] = resolved
    endpoint_text = str(endpoint).strip() if endpoint is not None else ""
    if endpoint_text:
        payload["endpoint"] = endpoint_text
    if not payload:
        return ""
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _case_seed_name(
    case_id: str,
    harness_seed: str | int,
    config: str = "",
) -> str:
    """Build the ``"<case_id>:<harness_seed>"`` UUID v5 name, plus ``config``."""
    case = case_id.strip()
    if not case:
        raise ValueError("case_id must be a non-empty string")
    name = f"{case}:{_seed_text(harness_seed)}"
    if config:
        name = f"{name}|{config}"
    return name


def derive_run_id(
    case_id: str,
    harness_seed: str | int,
    *,
    model_overrides: Any = None,
    resolved_models: Any = None,
    endpoint: str | None = None,
) -> str:
    """Derive the deterministic ``run_id`` for ``case_id`` and ``harness_seed``.

    The result is a UUID v5 string: the same inputs and the same
    result-affecting configuration always give the same id.  Two different
    model sets or endpoints for one case and seed therefore derive two
    distinct run ids.
    """
    config = _config_text(model_overrides, resolved_models, endpoint)
    name = _case_seed_name(case_id, harness_seed, config)
    identifier = uuid.uuid5(RUN_NAMESPACE, name)
    run_id = str(identifier)
    logger.debug(
        "identity: %s %s %s", f"{case_id = }", f"{harness_seed = }", f"{run_id = }"
    )
    return run_id


def derive_user_id(case_id: str, harness_seed: str | int) -> str:
    """Derive the deterministic ``user_id`` for ``case_id`` and ``harness_seed``."""
    name = _case_seed_name(case_id, harness_seed)
    identifier = uuid.uuid5(USER_NAMESPACE, name)
    user_id = str(identifier)
    logger.debug(
        "identity: %s %s %s", f"{case_id = }", f"{harness_seed = }", f"{user_id = }"
    )
    return user_id


def derive_chat_id(run_id: str, user_id: str) -> str:
    """Derive the deterministic ``chat_id`` for a run and its user.

    The name is ``"<run_id>:<user_id>"`` over :data:`CHAT_NAMESPACE`
    (ADR-0057, ADR-0060).
    """
    run_text = run_id.strip()
    user_text = user_id.strip()
    if not run_text or not user_text:
        raise ValueError("run_id and user_id must be non-empty strings")
    identifier = uuid.uuid5(CHAT_NAMESPACE, f"{run_text}:{user_text}")
    chat_id = str(identifier)
    logger.debug("identity: %s %s %s", f"{run_id = }", f"{user_id = }", f"{chat_id = }")
    return chat_id


@dataclass(frozen=True, slots=True)
class Identity:
    """The deterministic identifier triple of one evaluation run (ADR-0057)."""

    case_id: str
    harness_seed: str
    run_id: str
    user_id: str
    chat_id: str


def derive_identity(
    case_id: str,
    harness_seed: str | int,
    *,
    model_overrides: Any = None,
    resolved_models: Any = None,
    endpoint: str | None = None,
) -> Identity:
    """Derive the full :class:`Identity` for ``case_id`` and ``harness_seed``.

    ``model_overrides``, ``resolved_models`` and ``endpoint`` are the
    result-affecting configuration of the run: they enter the ``run_id`` name,
    so the same case and seed under two model sets derive two distinct runs.
    """
    run_id = derive_run_id(
        case_id,
        harness_seed,
        model_overrides=model_overrides,
        resolved_models=resolved_models,
        endpoint=endpoint,
    )
    user_id = derive_user_id(case_id, harness_seed)
    chat_id = derive_chat_id(run_id, user_id)
    identity = Identity(
        case_id=case_id,
        harness_seed=str(harness_seed),
        run_id=run_id,
        user_id=user_id,
        chat_id=chat_id,
    )
    logger.debug("identity: %s", f"{identity = }")
    return identity
