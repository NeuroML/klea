#!/usr/bin/env python3
"""
Client for the models.dev model catalog.

Fetches ``https://models.dev/api.json`` (a ~3MB JSON mapping of provider ->
model -> properties) and exposes the per-model token limits used to bound
LLM output token reservations.  This is needed because some providers
(e.g. HuggingFace) reserve the whole context window as output when no
max-token parameter is set, which leads to spurious usage limits and rate
limiting.

The catalog is fetched lazily on first use, kept in memory for the
process lifetime (``lru_cache``), and mirrored to an on-disk cache
(``{user_cache_dir}/klea/models-dev.json``) with a one-day TTL so that
restarts do not need to re-download it.

Providers without a catalog entry (local ollama, unknown custom
endpoints) and models missing from the catalog resolve to ``None`` so
callers can fall back gracefully instead of failing.

File: klea_utils/models_catalog.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail DOT com>
"""

from __future__ import annotations

import json
import logging
import os
import time
from functools import lru_cache
from pathlib import Path
from typing import NamedTuple

import httpx
from platformdirs import PlatformDirs

logger = logging.getLogger(__name__)

#: Default models.dev catalog URL.  Overridable via the KLEA_MODELS_DEV_URL
#: environment variable (e.g. for offline mirrors or enterprise proxies).
DEFAULT_MODELS_DEV_URL = "https://models.dev/api.json"

#: Time-to-live for the on-disk catalog cache, in seconds (1 day).
DISK_CACHE_TTL_SECONDS = 24 * 60 * 60

#: File name of the on-disk catalog cache, under the platformdirs cache dir.
DISK_CACHE_FILE = "models-dev.json"

#: HTTP timeout for fetching the catalog.  Short: this is only hit on the
#: first use after the disk cache expires.
FETCH_TIMEOUT_SECONDS = 15.0

#: Klea provider id -> models.dev catalog provider key.  Providers mapped
#: to ``None`` have no catalog entry (local ollama) and are handled as
#: "no information".  ``custom`` resolves to ``openai`` to mirror
#: ``LLMModel.build_config()``, which maps ``custom:`` model strings to
#: ``model_provider="openai"``.  Unknown providers are passed through
#: as-is and simply miss if the catalog does not contain them.
_MODELS_DEV_PROVIDER_KEYS: dict[str, str | None] = {
    "huggingface": "huggingface",
    "openai": "openai",
    "anthropic": "anthropic",
    "google_genai": "google",
    "google": "google",
    "custom": "openai",
    "ollama": None,
}


#: Provider ids whose endpoint must NOT be taken from the catalog, because
#: Klea has dedicated native handling for them (HuggingFace backend/provider
#: selection and token mapping).  Checked before any catalog lookup.  Others
#: are covered by the per-provider ``api`` guard: ``anthropic`` and ``openai``
#: carry no ``api`` in the catalog, so they already resolve to ``None``.
MODELS_DEV_PROVIDERS_IGNORED = {"huggingface"}


class ModelLimits(NamedTuple):
    """Token limits for a single model from the catalog.

    All fields are optional: the catalog always carries ``context`` and
    ``output``, while ``input`` is only defined for a subset of models.
    """

    context: int | None = None
    input: int | None = None
    output: int | None = None


def _catalog_provider_key(provider: str) -> str | None:
    """Map a Klea provider id to its models.dev catalog key.

    :param provider: Klea provider id (``huggingface``, ``openai``, ...)
    :returns: The models.dev catalog key, or ``None`` if the provider is
        known to have no catalog entry.
    """
    key = provider.lower()
    if key in _MODELS_DEV_PROVIDER_KEYS:
        return _MODELS_DEV_PROVIDER_KEYS[key]
    # Pass through unknown providers verbatim; lookups for them will
    # simply miss and return None.
    return key


def _catalog_url() -> str:
    """Return the models.dev catalog URL (env override or default)."""
    return os.getenv("KLEA_MODELS_DEV_URL", DEFAULT_MODELS_DEV_URL)


def _disk_cache_path() -> Path:
    """Return the on-disk cache path for the models.dev catalog.

    Shared by all Klea packages (``~/.cache/klea/models-dev.json`` on
    Linux) so a single download serves rag, code, and mcp processes.
    """
    return Path(PlatformDirs("klea").user_cache_dir) / DISK_CACHE_FILE


def _load_catalog_from_disk(cache_path: Path, allow_stale: bool = False) -> dict | None:
    """Load the catalog from the on-disk cache.

    :param cache_path: Path to the cache file.
    :param allow_stale: When ``False`` (default), return ``None`` for
        missing or expired caches.  When ``True``, also return expired
        caches (used as an offline fallback).
    :returns: The catalog dict, or ``None`` if it could not be loaded.
    """
    try:
        if not cache_path.exists():
            return None
        age_seconds = time.time() - cache_path.stat().st_mtime
        if not allow_stale and age_seconds > DISK_CACHE_TTL_SECONDS:
            logger.debug(
                "models.dev catalog cache is stale (%ds old), refetching",
                int(age_seconds),
            )
            return None
        with open(cache_path, "r") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            logger.warning("models.dev disk cache %s is not a JSON object", cache_path)
            return None
        logger.debug("Loaded models.dev catalog from disk cache: %s", cache_path)
        return data
    except Exception as e:  # noqa: BLE001
        logger.warning("Failed to read models.dev disk cache %s: %s", cache_path, e)
        return None


def _write_catalog_to_disk(cache_path: Path, data: dict) -> None:
    """Write the catalog to the on-disk cache (best-effort)."""
    try:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        with open(cache_path, "w") as f:
            json.dump(data, f)
        logger.debug("Wrote models.dev catalog to disk cache: %s", cache_path)
    except Exception as e:  # noqa: BLE001
        logger.warning("Failed to write models.dev disk cache %s: %s", cache_path, e)


def _fetch_catalog() -> dict:
    """Fetch the models.dev catalog from the network.

    Blocking with a short timeout: called only on first use after the
    disk cache expires.  Raises on network or parse failure.

    :returns: The catalog dict.
    """
    url = _catalog_url()
    logger.info("Fetching models.dev catalog from %s", url)
    resp = httpx.get(url, timeout=FETCH_TIMEOUT_SECONDS, follow_redirects=True)
    resp.raise_for_status()
    data = resp.json()
    if not isinstance(data, dict):
        raise TypeError("models.dev catalog is not a JSON object")
    return data


@lru_cache(maxsize=1)
def _catalog() -> dict:
    """Return the models.dev catalog, loading from cache or network.

    Cached in memory for the process lifetime (``lru_cache``), so the
    catalog is downloaded at most once per process.  A fresh disk cache
    short-circuits the network; on a network failure a stale disk copy
    is used as a fallback before propagating the error.

    :returns: The catalog dict.
    :raises: The underlying network/parse error if no disk copy exists.
    """
    cache_path = _disk_cache_path()

    data = _load_catalog_from_disk(cache_path)
    if data is not None:
        return data

    try:
        data = _fetch_catalog()
    except Exception as e:
        logger.warning("Failed to fetch models.dev catalog: %s", e)
        stale = _load_catalog_from_disk(cache_path, allow_stale=True)
        if stale is not None:
            logger.info("Falling back to stale models.dev catalog from disk")
            return stale
        raise

    _write_catalog_to_disk(cache_path, data)
    return data


def get_catalog_model_limits(provider: str, model_name: str) -> ModelLimits | None:
    """Return the token limits for a provider + model, or ``None``.

    Returns ``None`` when the provider has no catalog entry (local
    ollama, custom endpoints), the model is missing from the catalog, or
    the catalog could not be fetched.  Callers should treat ``None`` as
    "no information available".

    :param provider: Klea provider id (e.g. ``"huggingface"``).
    :param model_name: Model identifier, e.g. ``"gpt-4o"`` or
        ``"Qwen/Qwen3-Coder-30B-A3B-Instruct"``.
    :returns: ``ModelLimits`` with whatever fields the catalog defines.
    """
    catalog_key = _catalog_provider_key(provider)
    if catalog_key is None:
        return None

    try:
        catalog = _catalog()
    except Exception as e:  # noqa: BLE001
        logger.warning("models.dev catalog unavailable: %s", e)
        return None

    provider_entry = catalog.get(catalog_key)
    if not isinstance(provider_entry, dict):
        return None
    model_entry = provider_entry.get("models", {}).get(model_name)
    if not isinstance(model_entry, dict):
        return None
    limit = model_entry.get("limit")
    if not isinstance(limit, dict):
        return None

    return ModelLimits(
        context=limit.get("context") if isinstance(limit.get("context"), int) else None,
        input=limit.get("input") if isinstance(limit.get("input"), int) else None,
        output=limit.get("output") if isinstance(limit.get("output"), int) else None,
    )


class ProviderEndpoint(NamedTuple):
    """Endpoint metadata for one models.dev provider (optionally per model).

    ``api`` is the base URL an OpenAI/Anthropic-compatible client appends
    its resource path to (``/chat/completions``, ``/v1/messages``, ...), and
    ``npm`` is the AI-SDK package name, which identifies the wire protocol
    (``@ai-sdk/anthropic`` vs the OpenAI-shaped rest).  The catalog's ``env``
    field is deliberately not exposed: it names variables for the npm
    package, not for LangChain.

    Some providers (gateways such as OpenCode, OpenRouter, Cloudflare) serve
    different models on different wire surfaces.  The catalog records this as
    a per-model ``provider.npm`` override which, when present, wins over the
    provider default; :func:`get_provider_endpoint` resolves it when given a
    model name.
    """

    api: str | None = None
    npm: str | None = None


def get_provider_endpoint(
    provider: str, model_name: str | None = None
) -> ProviderEndpoint | None:
    """Return the endpoint metadata for a models.dev provider, or ``None``.

    Used to resolve ``provider:model`` model strings to an endpoint without
    the user supplying a URL (the catalog ``api``), mirroring how token
    limits are read from the same catalog.  Returns ``None`` when the
    provider has no catalog entry, the catalog is unavailable, or the entry
    carries no ``api`` (native SDK providers such as ``groq``/``mistral``
    resolve their own endpoint).

    When *model_name* is given and the catalog carries a per-model
    ``provider.npm`` override (as gateways do for models served on a
    different surface, e.g. ``muse-spark-1.2-contributor`` on OpenCode Go),
    that npm is returned instead of the provider default.

    :param provider: Klea provider id (e.g. ``"openrouter"``).
    :param model_name: Model identifier within the provider, or ``None``.
    :returns: :class:`ProviderEndpoint`, or ``None`` when unavailable.
    """
    try:
        catalog = _catalog()
    except Exception as e:  # noqa: BLE001
        logger.warning("models.dev catalog unavailable: %s", e)
        return None

    provider_entry = catalog.get(provider.lower())
    if not isinstance(provider_entry, dict):
        return None

    api = provider_entry.get("api")
    npm = provider_entry.get("npm")

    if model_name:
        model_entry = (provider_entry.get("models") or {}).get(model_name)
        if isinstance(model_entry, dict):
            model_provider = model_entry.get("provider")
            if isinstance(model_provider, dict) and model_provider.get("npm"):
                model_npm = model_provider["npm"]
                logger.debug(
                    f"Using per-model npm override for {provider}:{model_name}: "
                    f"{model_npm!r} (provider default {npm!r})"
                )
                npm = model_npm

    return ProviderEndpoint(
        api=api if isinstance(api, str) and api.strip() else None,
        npm=npm if isinstance(npm, str) and npm.strip() else None,
    )


#: Time-to-live for the in-memory live-endpoint model-limits cache, in
#: seconds.  A model's ``max_model_len`` is a stable property of its
#: deployment and rarely changes, so we cache it for the process lifetime;
#: this TTL is just a safety valve against very long-running servers.
ENDPOINT_LIMITS_CACHE_TTL_SECONDS = 12 * 60 * 60

#: HTTP timeout for the live-endpoint ``/models`` probe.  Short: it is
#: best-effort and must never stall a query.
ENDPOINT_LIMITS_FETCH_TIMEOUT_SECONDS = 5.0

#: Cache of live-endpoint model limits, keyed by ``(base_url, model_name)``
#: with an insertion timestamp so entries can expire after the TTL.
_ENDPOINT_LIMITS_CACHE: dict[tuple[str, str], tuple[float, ModelLimits]] = {}

#: LangChain provider id -> API key env var name, for providers whose id
#: does not map to ``{PROVIDER}_API_KEY``.  ``mistralai`` reads
#: ``MISTRAL_API_KEY`` in the langchain-mistralai SDK (not
#: ``MISTRALAI_API_KEY``).  The alias exists so the probe authenticates
#: with the same credential the model invoke would use.
_LANGCHAIN_PROVIDER_API_KEY_ENV: dict[str, str] = {"mistralai": "MISTRAL_API_KEY"}


def _langchain_provider_api_key_env(provider: str) -> str:
    """Return the API key env var name a LangChain SDK reads for *provider*.

    The provider id here is a LangChain ``_BUILTIN_PROVIDERS`` key, and
    the whole point is to mirror how the LangChain SDKs resolve
    credentials so the endpoint probe authenticates with the same key the
    model invoke would use.  Follows the ``{PROVIDER}_API_KEY`` convention
    unless an explicit alias is registered in
    :data:`_LANGCHAIN_PROVIDER_API_KEY_ENV`.
    """
    return (
        _LANGCHAIN_PROVIDER_API_KEY_ENV.get(provider) or f"{provider.upper()}_API_KEY"
    )


def probe_endpoint_model_limits(
    provider: str,
    model_name: str,
    base_url: str | None,
    api_key: str | None = None,
) -> ModelLimits | None:
    """Return token limits for a model served by a live OpenAI-compatible endpoint.

    models.dev's ``limit`` values are per-deployment configuration, not a
    property of the model, and private/custom endpoints (e.g. an internal
    vLLM server) are not in the catalog at all.  For those we ask the
    endpoint directly: ``GET {base_url}/models`` returns each model's
    ``max_model_len`` (the total context window, e.g. 262144 for a large
    vLLM deployment), which is the authoritative per-deployment value.

    Any provider with a known ``base_url`` is probed -- not just
    ``openai`` custom endpoints.  Native providers (``mistral:``,
    ``anthropic:``, ``deepseek:``, ...) normally carry no ``base_url`` in
    the configurable dict (their SDK resolves a default endpoint
    internally); callers resolve it via
    ``klea_utils.llm.resolve_langchain_endpoint`` and pass it here.  The
    probe is best-effort: providers whose ``/models`` response does not
    advertise ``max_model_len`` (e.g. Anthropic's non-OpenAI-shaped
    payload) simply return ``None`` and the catalog fallback is used.
    Results are cached in memory keyed by ``(base_url, model_name)`` for
    :data:`ENDPOINT_LIMITS_CACHE_TTL_SECONDS`; a user switching to a
    different model simply misses and triggers a fresh probe for the new
    key.

    Never raises and never blocks a query: any error, missing model, or
    inapplicable input returns ``None``.

    :param provider: Klea provider id (``openai`` for custom endpoints).
    :param model_name: Model identifier as served by the endpoint.
    :param base_url: Base URL of the OpenAI-compatible endpoint, or ``None``.
    :param api_key: Optional bearer token for the endpoint.
    :returns: ``ModelLimits`` with ``context`` set from ``max_model_len``,
        or ``None`` when the endpoint does not expose it or is not usable.
    """
    if not base_url:
        return None

    key = (base_url, model_name)
    now = time.monotonic()
    cached = _ENDPOINT_LIMITS_CACHE.get(key)
    if cached is not None and now - cached[0] < ENDPOINT_LIMITS_CACHE_TTL_SECONDS:
        return cached[1]

    context = _fetch_endpoint_max_model_len(base_url, model_name, provider, api_key)
    limits = ModelLimits(context=context) if context else None
    if limits is not None:
        _ENDPOINT_LIMITS_CACHE[key] = (now, limits)
    return limits


def _fetch_endpoint_max_model_len(
    base_url: str, model_name: str, provider: str, api_key: str | None
) -> int | None:
    """Fetch ``max_model_len`` for *model_name* from an OpenAI-compatible endpoint.

    Best-effort: returns ``None`` on any network, parse, or auth error so
    callers can fall back to the models.dev catalog or the built-in
    default without failing the query.  Also returns ``None`` when the
    endpoint's ``/models`` payload does not advertise ``max_model_len``
    (not all providers expose it in this OpenAI-shaped schema).

    When no explicit *api_key* is given, falls back to the standard
    ``{PROVIDER}_API_KEY`` environment variable derived from *provider*
    via :func:`_langchain_provider_api_key_env` (e.g. ``OPENAI_API_KEY``,
    ``MISTRAL_API_KEY``), matching how the LangChain/OpenAI SDKs resolve
    credentials for each provider.
    """
    resolved_key = api_key or os.environ.get(_langchain_provider_api_key_env(provider))
    headers = {"Authorization": f"Bearer {resolved_key}"} if resolved_key else {}
    url = base_url.rstrip("/") + "/models"
    try:
        resp = httpx.get(
            url,
            headers=headers,
            timeout=ENDPOINT_LIMITS_FETCH_TIMEOUT_SECONDS,
            follow_redirects=True,
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:  # noqa: BLE001
        logger.warning(
            f"Failed to fetch model limits from {url}: {e.__class__.__name__}"
        )
        return None

    entries = data.get("data") if isinstance(data, dict) else None
    if not isinstance(entries, list):
        logger.warning(f"Unexpected /models response shape from {url}")
        return None
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        if entry.get("id") == model_name and isinstance(
            entry.get("max_model_len"), int
        ):
            return entry["max_model_len"]
    logger.debug(f"No max_model_len for {model_name!r} in {url}")
    return None
