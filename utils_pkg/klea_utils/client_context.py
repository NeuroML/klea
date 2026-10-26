#!/usr/bin/env python3
"""
Runtime client context for a Klea chat run.

A TUI process can describe the environment its per-session bundled MCP server
should run in: the working directory, the interpreter, and the environment
variables.  The context is deliberately *not* persisted: it is per-run
runtime data, forwarded with each chat request and applied to the spawned
tool server for that run.

The server only accepts it from a loopback peer.  The context selects the
working directory and environment of a spawned child process, which is
equivalent to allowing arbitrary code selection, so a remote peer must not be
able to supply it.

See ``devdocs/adr/0055-runtime-client-mcp-context.md`` and
``devdocs/system/session-mcp-context.md``.

File: utils_pkg/klea_utils/client_context.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail DOT com>
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from klea_utils.errors import ClientContextError
from klea_utils.net import is_loopback_host

logger = logging.getLogger(__name__)

#: Maximum number of environment variables accepted in a client context.
MAX_ENV_VARS = 256

#: Maximum serialised size of the environment mapping, in UTF-8 bytes.
MAX_ENV_BYTES = 64 * 1024


class ClientContext(BaseModel):
    """Client-supplied environment for one chat run.

    All fields are optional, so a client can override only what it needs.
    An omitted working directory or interpreter leaves the backend's default
    in place; an omitted environment keeps the inherited environment (minus
    the virtual-environment variables stripped by
    :func:`klea_utils.mcp.spawn.bundled_server_config`).

    :param cwd: Working directory for the bundled MCP server.  Must be an
        absolute, existing directory once validated.
    :param env: Environment variables for the bundled MCP server.  The
        caller's values win over the inherited environment.
    :param interpreter: Python interpreter used to run the bundled MCP
        server.  Must be an absolute, executable file once validated.
    """

    model_config = ConfigDict(extra="forbid")

    cwd: str | None = None
    env: dict[str, str] = Field(default_factory=dict)
    interpreter: str | None = None


def _normalise_context(raw: Any) -> ClientContext:
    """Coerce *raw* to :class:`ClientContext`, raising a typed error."""
    if isinstance(raw, ClientContext):
        return raw
    try:
        return ClientContext.model_validate(raw)
    except ValidationError as exc:
        raise ClientContextError(f"Invalid client context: {exc}") from exc


def _validate_cwd(cwd: str) -> str:
    """Return the resolved *cwd*, or raise when it is unusable."""
    path = Path(cwd).expanduser()
    if not path.is_absolute():
        raise ClientContextError("client context cwd must be an absolute path")
    try:
        resolved = path.resolve(strict=True)
    except OSError as exc:
        raise ClientContextError(f"client context cwd does not exist: {path}") from exc
    if not resolved.is_dir():
        raise ClientContextError("client context cwd is not a directory")
    if not os.access(resolved, os.R_OK | os.X_OK):
        raise ClientContextError("client context cwd is not readable/executable")
    return str(resolved)


def _validate_interpreter(interpreter: str) -> str:
    """Return the resolved *interpreter*, or raise when it is unusable."""
    path = Path(interpreter).expanduser()
    if not path.is_absolute():
        raise ClientContextError("client context interpreter must be an absolute path")
    try:
        resolved = path.resolve(strict=True)
    except OSError as exc:
        raise ClientContextError(
            f"client context interpreter does not exist: {path}"
        ) from exc
    if not resolved.is_file():
        raise ClientContextError("client context interpreter is not a file")
    if not os.access(resolved, os.X_OK):
        raise ClientContextError("client context interpreter is not executable")
    return str(resolved)


def _validate_env(env: Mapping[str, str]) -> dict[str, str]:
    """Validate and return a copy of *env*, or raise when it is unusable."""
    if len(env) > MAX_ENV_VARS:
        raise ClientContextError(
            f"client context env has too many variables ({len(env)} > {MAX_ENV_VARS})"
        )
    validated: dict[str, str] = {}
    for key, value in env.items():
        if not isinstance(key, str) or not isinstance(value, str):
            raise ClientContextError(
                "client context env keys and values must be strings"
            )
        if not key:
            raise ClientContextError("client context env contains an empty name")
        if "=" in key or "\x00" in key or "\x00" in value:
            raise ClientContextError(
                f"client context env contains an invalid name/value: {key!r}"
            )
        validated[key] = value
    size = len(json.dumps(validated, ensure_ascii=False).encode("utf-8"))
    if size > MAX_ENV_BYTES:
        raise ClientContextError(
            f"client context env is too large ({size} > {MAX_ENV_BYTES} bytes)"
        )
    return validated


def validate_client_context(
    raw: ClientContext | Mapping[str, Any] | None,
    *,
    peer_host: str | None,
) -> ClientContext | None:
    """Validate a client-supplied context for the current request.

    The context is accepted only from a loopback peer.  A malformed context
    is rejected rather than silently trimmed, so a frontend never believes
    isolation is active when it is not.

    :param raw: The request's client context, already parsed or as a mapping.
    :param peer_host: The request socket peer host (``request.client.host``).
    :returns: The validated context, or ``None`` when *raw* is ``None`` or
        contains no fields.
    :raises ClientContextError: If the peer is not loopback or the context is
        malformed.
    """
    if raw is None:
        return None
    if not is_loopback_host(peer_host):
        raise ClientContextError("client context is only accepted from loopback peers")

    context = _normalise_context(raw)
    if context.cwd is None and context.interpreter is None and not context.env:
        return None

    cwd = _validate_cwd(context.cwd) if context.cwd is not None else None
    interpreter = (
        _validate_interpreter(context.interpreter)
        if context.interpreter is not None
        else None
    )
    env = _validate_env(context.env)

    validated = ClientContext(cwd=cwd, env=env, interpreter=interpreter)
    logger.debug(
        "Validated client context: cwd=%s interpreter=%s env_keys=%d",
        validated.cwd,
        validated.interpreter,
        len(validated.env),
    )
    return validated


def client_context_fingerprint(
    context: ClientContext | Mapping[str, Any] | None,
) -> str:
    """Return a stable fingerprint for *context*.

    Two contexts with the same cwd, interpreter, and environment (regardless
    of mapping order) produce the same fingerprint.  ``None`` and an empty
    context both produce the empty string.

    :param context: A validated context, a mapping, or ``None``.
    :returns: A hex digest, or ``""`` for no context.
    """
    if context is None:
        return ""
    normalised = _normalise_context(context)
    if normalised.cwd is None and normalised.interpreter is None and not normalised.env:
        return ""
    payload = {
        "cwd": normalised.cwd or "",
        "interpreter": normalised.interpreter or "",
        "env": dict(sorted(normalised.env.items())),
    }
    canonical = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def client_context_from_context(
    context: Any | None,
) -> ClientContext | None:
    """Return the client-context slice of a LangGraph runtime context.

    Tolerates a :class:`~klea_utils.graph.context.KleaRunContext`, a plain
    dict, or ``None``.  The value is not re-validated against the filesystem:
    it was validated at the request boundary.

    :param context: The runtime context from ``get_runtime().context``.
    :returns: The client context, or ``None`` when absent.
    """
    if context is None:
        return None
    if isinstance(context, Mapping):
        raw = context.get("client_context")
    else:
        raw = getattr(context, "client_context", None)
    if raw is None:
        return None
    return _normalise_context(raw)
