#!/usr/bin/env python3
"""
Agent tools caller

Klea Agent's variant of the shared
:class:`klea_utils.nodes.tools_caller.ToolsCallerNode`.  The pre-dispatch plan
section is built by the shared base hook, which deliberately emits only the
preformatted ``display`` fallback: the lower ``klea_utils`` package must not
import agent code.  This subclass overrides that hook to attach the structured
plan blocks, so the tools caller's ``key="plan"`` section carries the same
content blocks as the Planner's and the Evaluator's.  Everything else
dispatch, the access and path gates, the running-tools and end-of-round status
sections -- is inherited unchanged.  See ``devdocs/system/streams.md``.

File: klea_agent/nodes/agent_tools_caller.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail DOT com>
"""

import logging
from typing import override

from klea_utils.nodes.abstract import NodeStreamData
from klea_utils.nodes.tools_caller import ToolsCallerNode
from pydantic import BaseModel

from klea_agent.nodes.plan_content import plan_content_blocks

logger = logging.getLogger(__name__)


class AgentToolsCaller(ToolsCallerNode):
    """Shared tools caller that adds structured plan blocks to its plan event.

    The shared caller emits the ``key="plan"`` status section as preformatted
    ``display`` only.  This override attaches ``content=plan_content_blocks(
    plan)`` so frontends that understand content blocks render the same
    structured plan section as the Planner and the Evaluator, while the
    preformatted fallback keeps working for those that do not.
    """

    @override
    def _get_running_steps_state(self, state: BaseModel) -> NodeStreamData | None:
        """Attach the structured plan content blocks to the shared payload.

        Delegates the guard (no plan, an unrenderable plan, or no dispatched
        step numbers) to the shared hook, then adds the structured blocks to
        the payload it produced.  A subclass hook, so the shared ``klea_utils``
        caller stays free of agent imports.

        :param state: The graph state carrying the plan and the batch's calls.
        :returns: The plan status section with ``content`` set, or ``None`` when
            the base hook emits nothing for this round.
        """
        data = super()._get_running_steps_state(state)
        if data is None:
            logger.debug("no plan section: base hook returned None")
            return None
        plan = getattr(state, "plan", None)
        if plan is None:
            logger.debug("plan section without content: state carries no plan")
            return data
        logger.debug(
            "attaching plan content blocks: steps=%d status=%s",
            len(plan.step_list),
            plan.status,
        )
        return data.model_copy(update={"content": plan_content_blocks(plan)})
