#!/usr/bin/env python3
"""
Generic state-editing HITL helper for session commands.

A command can pause on a ``state_edit`` interrupt, show the user one or more
editable state fields, and apply the returned values when the run resumes.
The payload/response shape is intentionally generic so new commands can expose
additional state without a new frontend form:

* :class:`StateField` describes one editable field (``path_list``, ``text``,
  ``bool`` or ``select``);
* :class:`StateEditRequest` is the ``interrupt`` payload;
* :class:`StateEditResponse` is the reply (``save`` with ``values`` or
  ``cancel``).

Validation and application stay in the command handler; this module only
provides the transport and the fail-closed response parsing.

See ``devdocs/system/session-commands.md``; ADR-0047.

File: klea_utils/commands/state_edit.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail DOT com>
"""

import logging
from collections.abc import Callable
from typing import Any, Literal

from pydantic import BaseModel, Field, ValidationError

logger = logging.getLogger(__name__)

#: Editable field kinds understood by the web and TUI renderers.
StateFieldKind = Literal["path_list", "text", "bool", "select"]


class StateField(BaseModel):
    """One editable field in a :class:`StateEditRequest`.

    ``path_list`` renders a list editor (full paths, add/remove controlled by
    ``addable``/``removable``); ``text`` a single-line input; ``bool`` a switch;
    ``select`` a choice from ``options``.
    """

    key: str
    label: str
    kind: StateFieldKind = "text"
    value: Any = None
    help: str = ""
    options: list[str] = Field(default_factory=list)
    addable: bool = True
    removable: bool = True


class StateEditRequest(BaseModel):
    """The ``interrupt`` payload for a generic state editor."""

    kind: Literal["state_edit"] = "state_edit"
    title: str = "Edit session settings"
    description: str = ""
    fields: list[StateField] = Field(default_factory=list)


class StateEditResponse(BaseModel):
    """The user's reply to a :class:`StateEditRequest`.

    ``cancel`` leaves the state unchanged.  A response that fails validation
    is treated as a cancel by :func:`edit_state_fields`.
    """

    action: Literal["save", "cancel"] = "save"
    values: dict[str, Any] = Field(default_factory=dict)


def edit_state_fields(
    *,
    title: str,
    description: str = "",
    fields: list[StateField],
    interrupt_fn: Callable[[dict[str, Any]], Any] | None = None,
) -> dict[str, Any] | None:
    """Pause on a generic state editor and return the saved values.

    Uses the shared LangGraph ``interrupt()`` mechanism (ADR-0046) so the
    command node pauses and resumes in the same run.  A cancel, an invalid
    reply, or a reply with no values returns ``None``; the caller must then
    leave the state unchanged (fail closed).

    :param title: Editor heading.
    :param description: Optional explanatory text.
    :param fields: Fields to edit.
    :param interrupt_fn: Override for ``interrupt`` (tests); defaults to
        :func:`langgraph.types.interrupt`.
    :returns: The submitted ``values``, or ``None`` on cancel/failure.
    """
    if interrupt_fn is None:
        # Lazy: the command helper is imported outside a graph in unit tests.
        from langgraph.types import interrupt

        interrupt_fn = interrupt
    request = StateEditRequest(title=title, description=description, fields=fields)
    logger.debug(
        f"Requesting state edit\n{title = }\n{[field.key for field in fields] = }"
    )
    response = interrupt_fn(request.model_dump())
    if response is None:
        logger.debug("State edit cancelled (no response)")
        return None
    try:
        parsed = StateEditResponse.model_validate(response)
    except ValidationError as exc:
        logger.warning(f"Invalid state-edit response; leaving state unchanged: {exc}")
        return None
    if parsed.action == "cancel":
        logger.debug("State edit cancelled")
        return None
    logger.debug(f"State edit saved\n{list(parsed.values) = }")
    return parsed.values
