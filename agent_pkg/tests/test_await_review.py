#!/usr/bin/env python3
"""
Tests for the agent AwaitReview node.

File: tests/test_await_review.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail dot com>
"""

import logging

import pytest
from klea_agent.nodes.await_review import AwaitReview
from klea_agent.schemas import KleaAgentState


@pytest.mark.asyncio
async def test_await_review_returns_stub_feedback(monkeypatch):
    """The stub supplies a canned approval and streams progress/info."""
    node = AwaitReview(logging.getLogger("test"), "Awaiting review")
    emitted: list[dict] = []
    monkeypatch.setattr(node, "write_custom_stream", emitted.append)

    update = await node.execute(KleaAgentState())

    assert update["human_feedback"] == AwaitReview.STUB_REVIEW
    assert update["messages"][-1].content == AwaitReview.STUB_REVIEW
    assert emitted[0]["type"] == "progress"
    assert emitted[-1]["type"] == "inspect"
    assert emitted[-1]["data"]["details"]["human_feedback"] == AwaitReview.STUB_REVIEW
