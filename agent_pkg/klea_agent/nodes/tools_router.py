#!/usr/bin/env python3
"""
Tools router node

File: klea_agent/nodes/tools_router.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail DOT com>
"""

import logging
from typing import override

from klea_utils.nodes.abstract import (
    AbstractRouterNode,
    NodeStreamData,
    NodeStreamEvent,
)

from klea_agent.schemas import KleaAgentState


class ToolsRouter(AbstractRouterNode[KleaAgentState]):
    """Route based on tool call outputs.

    Inspects ``tool_results`` (``is_error`` per ADR-0003) and the current
    plan state to decide ``failed`` / ``explored`` / ``continue``.  Kept
    intentionally small — the evaluator split (plan + result evaluators)
    may change later.
    """

    def __init__(self, logger: logging.Logger, label: str):
        """Initialise the tools router node.

        :param logger: Logger instance
        :param label: Human-readable label for UI progress display
        """
        super().__init__(logger, label)

    @override
    async def execute(self, state: KleaAgentState) -> str:
        """Route based on tool call outputs.

        :param state: The current state
        :return: The routing label (``failed`` / ``explored`` / ``continue``)
        """
        self.write_custom_stream({"type": "progress", "node": self.label})
        self.logger.debug(f"{state = }")

        tool_results = getattr(state, "tool_results", []) or []
        has_error = any(getattr(r, "is_error", False) for r in tool_results)
        plan = getattr(state, "plan", None)

        if has_error:
            route = "failed"
        elif (
            plan is None
            or not getattr(plan, "step_list", None)
            or getattr(plan, "status", "") == "not_started"
        ):
            route = "explored"
        else:
            route = "continue"

        self.logger.debug(f"{route = }")

        info = NodeStreamData(
            heading="Tool Routing",
            summary=f"Routing: {route}",
            details={
                "route": route,
                "has_error": has_error,
                "tool_count": len(tool_results),
                "plan_status": getattr(plan, "status", None),
            },
        )
        self.write_custom_stream(
            NodeStreamEvent(type="info", node=self.label, data=info).model_dump()
        )

        return route
