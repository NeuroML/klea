#!/usr/bin/env python3
"""
Initialise graph state node

See ``devdocs/system/c4-component-agent.md``.

File: klea_agent/nodes/init_graph.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail DOT com>
"""

import logging
from typing import Any, override

from klea_utils.nodes.abstract import AbstractLangGraphNode
from klea_utils.nodes.context import NodeContext
from langchain_core.messages import HumanMessage

from klea_agent.discovery import refresh_project_files
from klea_agent.schemas import (
    CodeSchema,
    Discovery,
    KleaAgentState,
    RouteSchema,
)


class InitGraphState(
    AbstractLangGraphNode[KleaAgentState, dict[str, Any], NodeContext]
):
    """Initialise per-turn state before routing.

    Mirrors ``rag_pkg/klea_rag/nodes/init_rag.py`` for the non-task fields.
    Task/plan state is deliberately left intact here so ``RouteDecision`` can
    choose ``task`` (full reset via ``ResetState``) or ``continue`` (re-plan
    using prior evidence).  ``messages``,
    ``context_summary``/``summarised_till``, ``discovery_persistent``,
    session ``artefacts``, ``mode`` and ``usage_metrics`` are preserved.
    """

    def __init__(self, logger: logging.Logger, label: str):
        """Initialise with a logger."""
        super().__init__(logger, label)

    @override
    async def execute(self, state: KleaAgentState) -> dict[str, Any]:
        """Initialise per-turn fields and append the current query.

        Task/plan state (``plan``, ``goal``, ``step_outputs``, retry counters,
        feedback and transient tool fields) is not reset here; ``ResetState``
        applies the task boundary after ``RouteDecision``.  This preserves the
        previous run for a ``continue`` intent (ADR-0054).

        The current query is appended to ``messages`` (run history), which is
        preserved across turns so the Planner's memory and summarisation see
        the conversation.  A HITL resume does not re-run this node, so the
        query is recorded once per turn.

        ``artefacts`` is deliberately *not* reset: it is the session-scoped
        store that carries a task's durable result into later tasks in the same
        session (ADR-0035 update 2026-09-19).

        ``discovery_persistent`` is refreshed in place: the project instruction
        file (``AGENTS.md``/``CLAUDE.md``) is upserted, re-read only when it
        changes, so the Planner and answer-composer see current project
        conventions.  The file is read from the session ``project_root`` when
        one is set (the ``/cwd`` command), else the process working directory.
        """
        self._emit_progress()
        return {
            "guard_decision": "safe",
            "message_for_user": "",
            "command_route": "",
            "route": RouteSchema(),
            "discovery_persistent": refresh_project_files(
                state.discovery_persistent,
                project_root=state.project_root or None,
            ),
            "discovery_per_step": Discovery(),
            "code": CodeSchema(),
            "messages": [*state.messages, HumanMessage(content=state.query)],
        }
