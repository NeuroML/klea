#!/usr/bin/env python3
"""
Per-run runtime context for Klea graphs.

Carried by LangGraph's Runtime context mechanism (ADR-0033): the run
methods accept a ``context=`` value forwarded to ``ainvoke``/``astream``/
``astream_events``, and nodes read it inside execution via ambient
``get_runtime()``.  This is the framework-native replacement for the
hand-rolled ``model_overrides_ctx`` contextvar removed in ADR-0033; the
LLM's per-invocation ``RunnableConfig`` merge is unchanged (ADR-0014).

File: klea_utils/graph/context.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail DOT com>
"""

import logging
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

logger = logging.getLogger(__name__)


class KleaRunContext(BaseModel):
    """Per-run runtime context for one graph invocation.

    ``model_overrides`` is the conventional key the shared nodes in
    ``klea_utils.nodes`` consume (``chat_core`` populates it from the
    sessions database).  ``extra="allow"`` keeps the schema generic
    (ADR-0031): an app that wants its own keys validated at the run
    boundary subclasses this model and registers the subclass as its
    ``context_schema`` (LangGraph coerces the ``context`` dict via
    ``context_schema(**context)``); an app that prefers to validate
    custom keys itself reads them from ``model_extra``.
    """

    model_config = ConfigDict(extra="allow")

    #: Per-role model/API overrides; populated by ``chat_core`` from the
    #: sessions database (ADR-0014 decisions on sourcing, merge and key
    #: masking remain governing; ADR-0033 only changed the transport).
    model_overrides: dict[str, dict[str, Any]] = Field(default_factory=dict)


def model_overrides_from_context(
    context: KleaRunContext | dict[str, Any] | None,
) -> dict[str, dict[str, Any]]:
    """Return the ``model_overrides`` slice from a run context, or ``{}``.

    Tolerates a :class:`KleaRunContext` (attribute access), a plain dict
    (``context["model_overrides"]``), or ``None`` -- ``Runtime.context``
    *is* ``None`` when no ``context=`` was passed (ADR-0033,
    probe-verified), so the shared nodes must never see ``None``.

    :param context: The run context, e.g. ``get_runtime().context``.
    :returns: The overrides dict (never ``None``).
    """
    if context is None:
        return {}
    if isinstance(context, KleaRunContext):
        return context.model_overrides
    return context.get("model_overrides", {})
