# Copyright 2026 Ankur Sinha <sanjay DOT ankur AT gmail DOT com>
"""Apply a run's model selection to the backend (ADR-0057).

The run manager never touches the network, so the model overrides a run asks
for are applied here, before its first turn executes.  The helper drives the
same routes the dashboard uses (ADR-0042):

- ``GET /chat/{user_id}/{chat_id}/models/active`` reads the resolved model
  map per role;
- ``PUT /credentials/{user_id}`` stores one provider credential per distinct
  provider that the requested models need a key for;
- ``POST /chat/{user_id}/{chat_id}/models/overrides/{role}`` promotes one
  model into the chat's override for a role;
- a second ``GET`` of the active map verifies that every requested role now
  resolves with a credential, so a run never starts on a role that would fail
  on its first turn for want of a key.

The steps run in that order and a non-2xx answer aborts the sequence: nothing
is retried here (the run manager owns retries) and nothing is applied
partially once a request has failed.

API keys are read from the environment only, through the SDK-convention
helper of :mod:`klea_utils.llm`, and are pushed once per provider.  The secret
is never logged, never stored in a record and never echoed in an error
message: only provider names, env-var names and credential sources appear in
the logs.

The module imports ``httpx`` and ``klea_utils`` lazily, so importing it stays
cheap and the rest of the package keeps working without the SDK installed.
"""

from __future__ import annotations

import json
import logging
import os
from collections.abc import Mapping
from typing import Any
from urllib.parse import quote

logger = logging.getLogger(__name__)

__all__ = ["ModelOverrideError", "apply_run_models"]

#: Largest slice of a backend error detail echoed in an exception message.
_DETAIL_LIMIT = 500


class ModelOverrideError(Exception):
    """A run's model override could not be applied.

    Carries the role the failure belongs to (when known), the HTTP status the
    backend answered with (when a request was made) and the backend ``detail``
    verbatim, so a caller can render a precise message without re-parsing the
    response.
    """

    def __init__(
        self,
        message: str,
        *,
        role: str | None = None,
        status: int | None = None,
        detail: str | None = None,
    ) -> None:
        """Record the failure message and, when known, where it happened."""
        super().__init__(message)
        self.message = message
        self.role = role
        self.status = status
        self.detail = detail
        logger.debug(
            "run_config: model override failed (role=%s, status=%s): %s",
            role,
            status,
            message,
        )


def apply_run_models(
    client: Any,
    base_url: str,
    user_id: str,
    chat_id: str,
    models: Mapping[str, str],
    *,
    timeout: float | None = None,
    env: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """Apply ``models`` (role to model) to the chat and return the resolution.

    ``client`` is an injected HTTP client that belongs to its caller and is
    left open; pass ``None`` to have one created (and closed) here.  ``timeout``
    defaults to :data:`klea_eval.runners.http.DEFAULT_TIMEOUT_SECONDS`, the same
    value the HTTP runner uses.  ``env`` defaults to :data:`os.environ`.

    The returned mapping is the full active model map of the chat, as
    ``{role: model}``.  An empty ``models`` mapping makes no request at all.
    """
    requested = _normalise_models(models)
    if not requested:
        logger.debug("run_config: no model overrides requested")
        return {}

    base = _checked_base_url(base_url)
    user = _checked_identifier(user_id, "user_id")
    chat = _checked_identifier(chat_id, "chat_id")
    limit = _checked_timeout(timeout)
    environment = os.environ if env is None else env

    owns_client = client is None
    session = client
    if session is None:
        import httpx

        session = httpx.Client(timeout=limit)

    try:
        active = _read_active_models(session, base, user, chat, limit, owns_client)
        _require_roles(requested, active)

        secrets = _provider_secrets(requested, environment)
        for provider, secret in secrets.items():
            _push_credential(session, base, user, provider, secret, limit, owns_client)

        for role, model in requested.items():
            _set_override(session, base, user, chat, role, model, limit, owns_client)

        resolved = _read_active_models(session, base, user, chat, limit, owns_client)
        _require_credentials(resolved, requested)
    finally:
        if owns_client and session is not None:
            session.close()

    mapping = _role_models(resolved)
    logger.debug(
        "run_config: resolved %d role(s): %s", len(mapping), ", ".join(sorted(mapping))
    )
    return mapping


def _normalise_models(models: Any) -> dict[str, str]:
    """Validate the requested mapping into ``{role: model}``, stripped."""
    if models is None:
        return {}
    if not isinstance(models, Mapping):
        raise ModelOverrideError(
            f"model overrides must be a mapping, got {type(models).__name__}"
        )

    requested: dict[str, str] = {}
    for role, model in models.items():
        role_text = str(role).strip()
        if not role_text:
            raise ModelOverrideError("a model override declares an empty role")
        if not isinstance(model, str) or not model.strip():
            raise ModelOverrideError(
                f"model for role '{role_text}' must be a non-empty string",
                role=role_text,
            )
        requested[role_text] = model.strip()

    logger.debug("run_config: %d role(s) requested", len(requested))
    return requested


def _checked_base_url(base_url: Any) -> str:
    """Return the base URL with its trailing slashes removed."""
    text = str(base_url or "").strip().rstrip("/")
    if not text:
        raise ModelOverrideError("no base URL to apply the model overrides to")
    return text


def _checked_identifier(value: Any, label: str) -> str:
    """Return a mandatory, non-empty identifier."""
    text = str(value or "").strip()
    if not text:
        raise ModelOverrideError(f"{label} must be a non-empty string")
    return text


def _checked_timeout(timeout: Any) -> float:
    """Return the request timeout, defaulting to the runner's own value."""
    if timeout is None:
        from klea_eval.runners.http import DEFAULT_TIMEOUT_SECONDS

        return float(DEFAULT_TIMEOUT_SECONDS)

    invalid = (
        isinstance(timeout, bool)
        or not isinstance(timeout, (int, float))
        or timeout <= 0
    )
    if invalid:
        raise ModelOverrideError(f"timeout must be a positive number, got {timeout!r}")
    return float(timeout)


def _send(
    request: Any,
    url: str,
    *,
    timeout: float,
    owner: bool,
    body: Mapping[str, Any] | None = None,
) -> Any:
    """Perform one request, mapping transport failures to a local error.

    A client created here receives the per-request timeout; an injected client
    is configured by its owner and is called without one.  The request body is
    never echoed in the error, so a pushed secret cannot leak through it.
    """
    kwargs: dict[str, Any] = {}
    if owner:
        kwargs["timeout"] = timeout
    if body is not None:
        kwargs["json"] = dict(body)

    try:
        return request(url, **kwargs)
    except Exception as exc:
        raise ModelOverrideError(
            f"request to {url} failed: {type(exc).__name__}: {exc}"
        ) from exc


def _read_active_models(
    client: Any,
    base: str,
    user: str,
    chat: str,
    timeout: float,
    owner: bool,
) -> dict[str, Any]:
    """Read the chat's resolved model map from the backend."""
    url = f"{base}/chat/{quote(user, safe='')}/{quote(chat, safe='')}/models/active"
    response = _send(client.get, url, timeout=timeout, owner=owner)
    _ensure_success(response, "GET", url)

    payload = _response_payload(response, "GET", url)
    if not isinstance(payload, Mapping):
        raise ModelOverrideError(f"GET {url} returned a non-object model map")

    logger.debug("run_config: read %d active role(s)", len(payload))
    return dict(payload)


def _require_roles(requested: Mapping[str, str], active: Mapping[str, Any]) -> None:
    """Reject a role the backend does not expose before anything is pushed."""
    unknown = [role for role in requested if role not in active]
    if not unknown:
        return

    exposed = sorted(str(role) for role in active)
    exposed_text = ", ".join(exposed) if exposed else "none"
    raise ModelOverrideError(
        f"unknown role '{unknown[0]}' (the backend exposes: {exposed_text})",
        role=unknown[0],
    )


def _provider_secrets(
    requested: Mapping[str, str], environment: Mapping[str, str]
) -> dict[str, str]:
    """Collect one secret per provider that needs one, from the environment."""
    secrets: dict[str, str] = {}
    skipped: set[str] = set()

    for model in requested.values():
        provider = _provider_of(model)
        if provider is None or provider in secrets or provider in skipped:
            continue

        env_var = _env_var_for(provider)
        if env_var is None:
            skipped.add(provider)
            logger.debug("run_config: provider '%s' needs no API key", provider)
            continue

        secret = environment.get(env_var)
        if not isinstance(secret, str) or not secret.strip():
            skipped.add(provider)
            logger.debug("run_config: %s is unset for provider '%s'", env_var, provider)
            continue

        secrets[provider] = secret
        logger.debug("run_config: found a credential for provider '%s'", provider)

    return secrets


def _provider_of(model: str) -> str | None:
    """Return the ``<provider>`` half of a ``<provider>:<name>`` model string."""
    head, separator, _ = model.partition(":")
    if not separator:
        return None

    provider = head.strip()
    return provider or None


def _env_var_for(provider: str) -> str | None:
    """Return the SDK-convention env var of ``provider``, or ``None``.

    The naming rule belongs to :func:`klea_utils.llm.provider_api_key_env`; it is
    imported here rather than re-derived, so a provider that needs no key (a
    local runtime, for example) keeps returning ``None``.
    """
    try:
        from klea_utils.llm import provider_api_key_env
    except ImportError as exc:
        raise ModelOverrideError(
            f"could not import the provider key helper: {exc}"
        ) from exc

    name = provider_api_key_env(provider)
    if isinstance(name, str) and name.strip():
        return name.strip()
    return None


def _push_credential(
    client: Any,
    base: str,
    user: str,
    provider: str,
    secret: str,
    timeout: float,
    owner: bool,
) -> None:
    """Store one provider credential for the run's user."""
    url = f"{base}/credentials/{quote(user, safe='')}"
    body = {"provider": provider, "endpoint": "", "secret": secret}

    response = _send(client.put, url, timeout=timeout, owner=owner, body=body)
    _ensure_success(response, "PUT", url)

    logger.debug("run_config: stored a credential for provider '%s'", provider)


def _set_override(
    client: Any,
    base: str,
    user: str,
    chat: str,
    role: str,
    model: str,
    timeout: float,
    owner: bool,
) -> None:
    """Promote ``model`` into the chat's override of ``role``."""
    url = (
        f"{base}/chat/{quote(user, safe='')}/{quote(chat, safe='')}"
        f"/models/overrides/{quote(role, safe='')}"
    )
    body = {"model": model, "promote_default": False}

    response = _send(client.post, url, timeout=timeout, owner=owner, body=body)
    _ensure_success(response, "POST", url, role=role)

    logger.debug("run_config: set the override of role '%s'", role)


def _require_credentials(
    active: Mapping[str, Any], requested: Mapping[str, str]
) -> None:
    """Reject a requested role the backend still reports as keyless-credential."""
    for role in requested:
        config = active.get(role)
        if not isinstance(config, Mapping):
            raise ModelOverrideError(
                f"role '{role}' is missing from the active model map", role=role
            )

        credential = config.get("credential")
        if not isinstance(credential, Mapping):
            continue

        requires = bool(credential.get("requires_key"))
        source = credential.get("source")
        source_text = source.strip().lower() if isinstance(source, str) else ""
        if requires and source_text == "none":
            raise ModelOverrideError(
                f"role '{role}' still requires an API key after the override; "
                "no stored or environment credential was accepted",
                role=role,
            )


def _role_models(active: Mapping[str, Any]) -> dict[str, str]:
    """Flatten the active model map into ``{role: model}``."""
    mapping: dict[str, str] = {}
    for role, config in active.items():
        model = config.get("model") if isinstance(config, Mapping) else None
        mapping[str(role)] = model if isinstance(model, str) else ""
    return mapping


def _ensure_success(
    response: Any, method: str, url: str, *, role: str | None = None
) -> None:
    """Raise a :class:`ModelOverrideError` for a 4xx or 5xx answer."""
    status = getattr(response, "status_code", None)
    if isinstance(status, bool) or not isinstance(status, int) or status < 400:
        return

    detail = _response_detail(response)
    message = f"{method} {url} answered {status}"
    if detail:
        message = f"{message}: {detail}"
    raise ModelOverrideError(message, role=role, status=status, detail=detail or None)


def _response_detail(response: Any) -> str:
    """Return the backend ``detail`` of a failed response, verbatim."""
    payload: Any = None
    try:
        payload = response.json()
    except Exception:  # noqa: BLE001
        payload = None

    detail = payload.get("detail") if isinstance(payload, Mapping) else None
    if detail is not None:
        text = _as_text(detail)
        if text:
            return text

    return _body_text(response)


def _as_text(value: Any) -> str:
    """Render an error detail as one bounded ASCII string."""
    if isinstance(value, str):
        return value.strip()[:_DETAIL_LIMIT]
    try:
        rendered = json.dumps(value, sort_keys=True, ensure_ascii=True, default=str)
    except (TypeError, ValueError):
        return str(value)[:_DETAIL_LIMIT]
    return rendered[:_DETAIL_LIMIT]


def _body_text(response: Any) -> str:
    """Return the bounded body of a failed response, or an empty string."""
    try:
        text = response.text
    except Exception:  # noqa: BLE001
        return ""
    if not isinstance(text, str):
        return ""
    return text.strip()[:_DETAIL_LIMIT]


def _response_payload(response: Any, method: str, url: str) -> Any:
    """Return the parsed body of a successful response."""
    try:
        return response.json()
    except Exception as exc:
        raise ModelOverrideError(
            f"{method} {url} did not return JSON: {type(exc).__name__}: {exc}"
        ) from exc
