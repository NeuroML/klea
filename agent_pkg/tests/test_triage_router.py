#!/usr/bin/env python3
"""
Tests for the agent TriageRouter and the ADaPT retry counter.

File: tests/test_triage_router.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail dot com>
"""

import logging

import pytest
from fastmcp.client.client import CallToolResult
from klea_agent.nodes.triage_router import (
    TriageRouter,
    current_step_key,
    update_tool_retry_counts,
)
from klea_agent.schemas import KleaAgentState, PlanSchema, StepSchema


def _state(*, error: bool, step: int = 0, counts: dict[int, int] | None = None):
    state = KleaAgentState()
    state.tool_results = [
        CallToolResult(content=[], structured_content=None, meta=None, is_error=error)
    ]
    # A plan with one step per index; the step identity is its 1-based number.
    state.plan = PlanSchema(
        step_list=[
            StepSchema(step_number=i + 1, description=f"s{i + 1}")
            for i in range(step + 1)
        ],
        current_step_index=step,
    )
    state.tool_retry_counts = counts or {}
    return state


class TestRetryCounts:
    """The consecutive-failure counter updated by the caller callback."""

    def test_error_increments(self):
        counts = update_tool_retry_counts(_state(error=True))
        assert counts == {1: 1}

    def test_repeated_errors_accumulate(self):
        counts = update_tool_retry_counts(_state(error=True, counts={1: 2}))
        assert counts == {1: 3}

    def test_success_clears_budget(self):
        counts = update_tool_retry_counts(_state(error=False, counts={1: 2}))
        assert counts == {}

    def test_step_key_tracks_step_number(self):
        assert current_step_key(_state(error=False, step=3)) == 4
        counts = update_tool_retry_counts(_state(error=True, step=3))
        assert counts == {4: 1}


class TestTriageDecide:
    """Pure routing policy."""

    def setup_method(self):
        self.router = TriageRouter(
            logging.getLogger("test.triage"), "Triage", max_retries=2
        )

    def test_no_error_evaluates(self):
        assert self.router.decide(_state(error=False)) == "evaluate"

    def test_error_within_budget_retries(self):
        assert self.router.decide(_state(error=True, counts={1: 1})) == "retry"
        assert self.router.decide(_state(error=True, counts={1: 2})) == "retry"

    def test_error_over_budget_replans(self):
        assert self.router.decide(_state(error=True, counts={1: 3})) == "replan"

    def test_error_without_counter_retries(self):
        # Counter not yet maintained: treat as first failure.
        assert self.router.decide(_state(error=True)) == "retry"


class TestTriageExecute:
    """Node execution streams and returns the same route."""

    @pytest.mark.asyncio
    async def test_execute_streams_info(self, monkeypatch):
        router = TriageRouter(logging.getLogger("test.triage"), "Triage")
        emitted: list[dict] = []
        monkeypatch.setattr(router, "write_custom_stream", emitted.append)

        route = await router.execute(_state(error=True, counts={1: 9}))

        assert route == "replan"
        assert emitted[0]["type"] == "progress"
        assert emitted[-1]["type"] == "inspect"
        assert emitted[-1]["data"]["details"]["route"] == "replan"
