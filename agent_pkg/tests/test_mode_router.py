#!/usr/bin/env python3
"""
Tests for the agent operating-mode decision node (ADR-0030).

File: tests/test_mode_router.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail DOT com>
"""

import logging

import pytest
from klea_agent.nodes.mode_router import ModeDecision, decide_mode
from klea_agent.schemas import KleaAgentState, Mode


class TestDecideMode:
    """Pure decision logic."""

    def setup_method(self):
        self.logger = logging.getLogger(f"{__name__}.{self.__class__.__name__}")

    def test_default_general(self):
        resolved, note = decide_mode("general", source_available=False)
        assert resolved == "general"
        assert note == ""

    def test_scientific_without_source_does_not_silently_downgrade(self):
        # No curated source: the task must not proceed as a verified
        # scientific result, and the user is told why (ADR-0030).
        resolved, note = decide_mode("scientific", source_available=False)
        assert resolved == "general"
        assert note

    def test_scientific_with_source(self):
        # With a curated source scientific mode is entered; verification /
        # assurance tracking is deferred to the ADR-0029 phase.
        resolved, note = decide_mode("scientific", source_available=True)
        assert resolved == "scientific"
        assert note == ""


class TestModeDecisionNode:
    """Node behaviour: state updates + context event."""

    def setup_method(self):
        self.logger = logging.getLogger(f"{__name__}.{self.__class__.__name__}")

    @pytest.mark.asyncio
    async def test_execute_returns_state_updates_only(self, monkeypatch):
        """execute writes state; the graph streamer owns the context event."""
        node = ModeDecision(self.logger, "Determining mode")
        emitted: list[dict] = []
        monkeypatch.setattr(node, "_pre_exec_stream", lambda: None)
        monkeypatch.setattr(node, "write_custom_stream", lambda ev: emitted.append(ev))

        state = KleaAgentState(query="q", mode=Mode(requested="scientific"))
        result = await node.execute(state)

        # State updates only -- no context event authored by the node
        # (ADR-0032: context is derived from state by context_snapshot).
        mode = result["mode"]
        assert mode.requested == "scientific"
        assert mode.resolved == "general"
        assert mode.note
        assert emitted == []
