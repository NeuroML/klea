#!/usr/bin/env python3
"""
Explore planner node for KleaAgent

File: klea_agent/nodes/explore_planner.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail DOT com>
"""

from typing import Any, override

from pydantic import BaseModel

from klea_agent.nodes.planner import Planner
from klea_agent.schemas import PlanSchema


class ExplorePlanner(Planner):
    """Node that plans exploration steps for a codebase.

    Subclasses ``Planner`` (so it inherits ``BaseLLMNode`` handling of
    ``tools_info``/prompt plumbing) but overrides the prompt variables to
    focus on discovery.  This node is intentionally kept as a separate
    stage for now; a future architectural decision may replace it with
    direct tool calls / sub-agents or fold it into ``Planner``.  Until
    that ADR, it stays as exploration-planning only and does not itself
    execute tools.
    """

    model_type = "plan"

    @override
    def __init__(self, logger, label: str, llm_models: dict[str, Any]):
        """Initialise the explore planner node.

        :param logger: Logger instance
        :param label: Human-readable label for UI progress display
        :param llm_models: ``{role: LLMModel}`` dict (from ``BaseLangGraph.llm_models``)
        """
        super().__init__(logger=logger, label=label, llm_models=llm_models)
        self.prompt_prefix = "ExplorePlanner"

    @override
    def _get_prompt_variables(self, state: BaseModel) -> dict:
        """Format prompt with current state."""
        # TODO: limit to required state field
        return {
            "query": getattr(state, "query", ""),
            "goal": getattr(state, "goal", ""),
            "step_list": getattr(getattr(state, "plan", None), "step_list", []),
            "current_step_index": getattr(
                getattr(state, "plan", None), "current_step_index", 0
            ),
            "discovery": getattr(state, "discovery_persistent", {}),
            "discovery_last_step": getattr(state, "discovery_per_step", {}),
            "observations": getattr(state, "step_outputs", {}),
        }

    @override
    def _update_state(self, result: PlanSchema, state: BaseModel) -> dict[str, Any]:
        """Update exploration_plan in state."""
        return {"plan": result}

    @override
    def _get_default_error_result(self) -> PlanSchema:
        """Return default result when processing fails."""
        return PlanSchema(status="failed")
