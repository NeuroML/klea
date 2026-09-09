#!/usr/bin/env python3
"""
Operating-mode decision node (ADR-0030).

Decides the operating mode (Scientific vs General) and the resulting
assurance level at task entry, and informs the user when a requested
mode cannot run instead of silently downgrading.

File: klea_agent/nodes/mode_router.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail DOT com>
"""

import logging
from typing import Any, Literal, override

from klea_utils.nodes.abstract import (
    AbstractLangGraphNode,
    NodeStreamData,
    NodeStreamEvent,
)

from klea_agent.schemas import KleaAgentState, Mode


def decide_mode(
    requested: str, *, source_available: bool
) -> tuple[Literal["general", "scientific"], str]:
    """Resolve the operating mode (ADR-0030).

    Returns a ``(resolved, note)`` pair:

    * explicit or default ``general``: ``resolved=general``.
    * ``scientific`` with an approved curated knowledge source:
      ``resolved=scientific``.  The ADR-0029 verification workflow is not
      wired yet, so verification/assurance tracking is deferred.
    * ``scientific`` without a curated source: does **not** silently
      downgrade.  ``resolved=general`` with a ``note`` explaining that
      verification cannot be established, so the graph informs the user
      and stops rather than produce a plausible but unverified scientific
      answer.

    :param requested: The explicit mode request (``general`` /
        ``scientific``).
    :param source_available: Whether an approved curated knowledge source is
        available (Scientific mode precondition, ADR-0030 invariant 1).
    :returns: ``(resolved, note)`` -- the resolved mode and a note when the
        request cannot be honoured.
    """
    requested = (requested or "general").strip().lower()
    if requested == "scientific" and source_available:
        return ("scientific", "")
    if requested == "scientific":
        return (
            "general",
            (
                "Scientific mode requires an approved curated knowledge "
                "source, which is not configured. This task has not been "
                "run as a verified scientific workflow. Provide a curated "
                "source, or explicitly re-run in general mode."
            ),
        )
    return ("general", "")


class ModeDecision(AbstractLangGraphNode[KleaAgentState, dict[str, Any]]):
    """Decide the operating mode at task entry.

    Writes the resolution into the state (``mode.resolved``, plus
    ``mode.note`` when the request cannot be honoured) only -- this node
    emits no events.  The graph-level ``context`` stream event is derived
    from this state by :meth:`BaseLangGraph.context_snapshot`
    (ADR-0032): session context is a projection of state, and nodes
    cannot author ``context`` events themselves.
    """

    def __init__(
        self, logger: logging.Logger, label: str, *, source_available: bool = False
    ):
        """Initialise.

        :param logger: Logger instance
        :param label: Human-readable label for UI progress display
        :param source_available: Whether an approved curated knowledge
            source is configured (Scientific mode precondition).
        """
        super().__init__(logger, label)
        self._source_available = source_available

    @override
    async def execute(self, state: KleaAgentState) -> dict[str, Any]:
        """Resolve the mode, persisting the ask and the resolution."""
        self._pre_exec_stream()
        resolved, note = decide_mode(
            state.mode.requested, source_available=self._source_available
        )
        self.logger.debug(
            f"mode decision: requested={state.mode.requested} -> {resolved}"
        )
        return {
            "mode": Mode(requested=state.mode.requested, resolved=resolved, note=note)
        }


class ModeInformer(AbstractLangGraphNode[KleaAgentState, dict[str, Any]]):
    """Inform the user when a requested mode cannot run.

    Terminal node for the ADR-0030 inform-or-restart branch: sets
    ``message_for_user`` to the mode ``note`` and stops (the task does
    not proceed to a plausible-but-unverified answer).
    """

    @override
    async def execute(self, state: KleaAgentState) -> dict[str, Any]:
        """Return the explanatory note as the final message."""
        self._pre_exec_stream()
        note = state.mode.note or "Scientific mode is unavailable."
        self.logger.debug(f"informing user about mode: {note}")
        info = NodeStreamData(heading="Mode", summary=note)
        self.write_custom_stream(
            NodeStreamEvent(type="info", node=self.label, data=info).model_dump()
        )
        return {"message_for_user": note}
