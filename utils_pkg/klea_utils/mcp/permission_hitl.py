#!/usr/bin/env python3
"""
HITL payload and response schema for interactive path approval (ADR-0007).

The client-side permission gate collects outside-project ``PathRequest``
objects, pauses the run with an ``interrupt`` carrying
:func:`build_permission_payload`, and resolves the user's per-path
allow/deny decision with :func:`resolve_decisions`.  The interrupt mechanism
itself is the shared LangGraph one (ADR-0046); this module owns only the
permission-specific payload, response schema, and decision mapping.

See ``devdocs/system/mcp-permissions.md``.

File: klea_utils/mcp/permission_hitl.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail DOT com>
"""

import logging
from collections.abc import Sequence
from typing import Any, Literal

from pydantic import BaseModel, Field, ValidationError

from klea_utils.mcp.path_detect import PathRequest

logger = logging.getLogger(__name__)

#: Default question shown with a permission ask.
DEFAULT_QUESTION = (
    "The agent wants to access paths outside the project directory. "
    "Allow or deny each one."
)

#: A per-path decision: allow for this dispatch only, allow for the session
#: (persisted in ``allowed_dirs``), or deny.
Decision = Literal["once", "session", "deny"]


class PathDecision(BaseModel):
    """A user's decision for one requested directory.

    ``directory`` matches a request's resolved directory; ``decision`` is
    ``once`` (this dispatch), ``session`` (also persisted for the thread), or
    ``deny``.
    """

    directory: str
    decision: Decision = "deny"


class PermissionResponse(BaseModel):
    """Resume payload for a permission ask (ADR-0007).

    ``action="cancel"`` denies every request.  ``action="answer"`` carries one
    :class:`PathDecision` per requested directory; a requested directory with
    no decision is denied (fail closed).
    """

    action: Literal["answer", "cancel"] = "answer"
    decisions: list[PathDecision] = Field(default_factory=list)


class PermissionResolution(BaseModel):
    """Resolved decisions for one permission ask.

    :attr:`allowed_now` is allowed for this dispatch only;
    :attr:`allowed_session` is also persisted for the thread; :attr:`denied`
    is refused (the gate turns each into a non-halting denial).
    """

    allowed_now: list[str] = Field(default_factory=list)
    allowed_session: list[str] = Field(default_factory=list)
    denied: list[str] = Field(default_factory=list)

    def effective_dirs(self) -> list[str]:
        """Return the directories allowed for the current dispatch."""
        return [*self.allowed_now, *self.allowed_session]


def build_permission_payload(
    requests: Sequence[PathRequest], *, question: str = ""
) -> dict[str, Any]:
    """Build the interrupt payload for a list of path requests.

    The payload is a plain mapping (the stream/interrupt convention): ``kind``
    is ``"permission"``, ``question`` is the prompt text, and ``requests`` is
    the list of serialized :class:`~klea_utils.mcp.path_detect.PathRequest`
    objects for the client to render.

    :param requests: The outside-path requests to ask about.
    :param question: Custom prompt text; defaults to :data:`DEFAULT_QUESTION`.
    :returns: The interrupt payload dict.
    """
    return {
        "kind": "permission",
        "question": question or DEFAULT_QUESTION,
        "requests": [request.model_dump() for request in requests],
    }


def _coerce_response(response: Any) -> PermissionResponse:
    """Return *response* as a :class:`PermissionResponse`, fail-closed.

    Accepts an already-validated model, a raw mapping (as ``chat_core`` builds
    from the client answer), or anything else; an invalid response becomes an
    empty answer, which denies every request.
    """
    if isinstance(response, PermissionResponse):
        return response
    if isinstance(response, dict):
        try:
            return PermissionResponse.model_validate(response)
        except ValidationError:
            logger.warning("Invalid permission response; denying all requests")
    return PermissionResponse()


def resolve_decisions(
    requests: Sequence[PathRequest], response: Any
) -> PermissionResolution:
    """Resolve a resume value into allow-now / allow-session / deny sets.

    Uses each request's resolved directory as the key.  A cancel, an invalid
    response, or a request with no matching decision denies that request (fail
    closed).

    :param requests: The requests that were asked about.
    :param response: The value returned by the interrupt (a
        :class:`PermissionResponse`, a raw mapping, or ``None``).
    :returns: The :class:`PermissionResolution`.
    """
    parsed = _coerce_response(response)
    requested = [request.directory for request in requests]
    if parsed.action == "cancel":
        logger.info(f"Permission ask cancelled; denying all\n{requested = }")
        return PermissionResolution(denied=list(requested))

    by_directory = {
        decision.directory: decision.decision for decision in parsed.decisions
    }
    resolution = PermissionResolution()
    for directory in requested:
        decision = by_directory.get(directory, "deny")
        if decision == "session":
            resolution.allowed_session.append(directory)
        elif decision == "once":
            resolution.allowed_now.append(directory)
        else:
            resolution.denied.append(directory)
    logger.debug(f"Resolved permission ask\n{resolution = }")
    return resolution
