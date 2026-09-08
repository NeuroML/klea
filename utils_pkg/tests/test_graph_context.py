#!/usr/bin/env python3
"""
Tests for the per-run runtime context (ADR-0033).

Covers the shared ``KleaRunContext`` schema and the
``model_overrides_from_context`` read helper.

File: tests/test_graph_context.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail DOT com>
"""

import logging
from typing import Any

from klea_utils.graph.context import KleaRunContext, model_overrides_from_context
from langgraph.graph import END, START, StateGraph
from langgraph.runtime import get_runtime
from pydantic import BaseModel, Field


def _overrides() -> dict[str, dict[str, Any]]:
    return {"chat": {"model": "test-model", "api_key": "secret"}}


class TestKleaRunContext:
    """The shared context schema."""

    def setup_method(self):
        self.logger = logging.getLogger(f"{__name__}.{self.__class__.__name__}")

    def test_default_overrides_empty(self):
        """model_overrides defaults to an empty dict."""
        ctx = KleaRunContext()
        assert ctx.model_overrides == {}

    def test_overrides_roundtrip(self):
        """model_overrides survives construction."""
        ctx = KleaRunContext(model_overrides=_overrides())
        assert ctx.model_overrides == _overrides()

    def test_extra_allow_keeps_custom_keys(self):
        """extra='allow' lets an app carry custom per-run keys."""
        ctx = KleaRunContext(model_overrides={}, custom_key={"n": 1})
        assert ctx.model_extra == {"custom_key": {"n": 1}}


class AppRunContext(KleaRunContext):
    """An app may subclass for boundary-validated custom keys."""

    user_locale: str = "en"


class _AmbientState(BaseModel):
    out: dict[str, Any] = Field(default_factory=dict)


_AMBIENT_SEEN: list[Any] = []


async def _ambient_node(state: _AmbientState) -> dict[str, Any]:
    """Async node reading the runtime context ambiently (no ``runtime`` param)."""
    rt = get_runtime()
    _AMBIENT_SEEN.append(rt.context)
    ctx = rt.context
    return {"out": dict(ctx.model_overrides) if ctx else {}}


def _ambient_graph():
    graph = StateGraph(_AmbientState, context_schema=KleaRunContext)
    graph.add_node("n", _ambient_node)
    graph.add_edge(START, "n")
    graph.add_edge("n", END)
    return graph.compile()


class TestAmbientRuntimeContext:
    """End-to-end: the pydantic context reaches an async node on both paths.

    Codifies the ADR-0033 probes so the framework behaviour never silently
    regresses (dict context coerced via context_schema(**context), read
    through ambient ``get_runtime()``).
    """

    async def test_ainvoke_ambient_context(self):
        _AMBIENT_SEEN.clear()
        ov = {"chat": {"model": "x"}}
        res = await _ambient_graph().ainvoke(
            {"out": {}}, context={"model_overrides": ov}
        )
        assert res["out"] == ov
        assert isinstance(_AMBIENT_SEEN[-1], KleaRunContext)
        assert _AMBIENT_SEEN[-1].model_overrides == ov

    async def test_astream_events_v3_ambient_context(self):
        _AMBIENT_SEEN.clear()
        ov = {"chat": {"model": "y"}}
        stream = await _ambient_graph().astream_events(
            {"out": {}}, version="v3", context={"model_overrides": ov}
        )
        async for _e in stream:
            pass
        assert _AMBIENT_SEEN and _AMBIENT_SEEN[-1].model_overrides == ov

    async def test_ainvoke_no_context_yields_none(self):
        """Without context, Runtime.context is None; the helper normalizes it."""
        _AMBIENT_SEEN.clear()
        await _ambient_graph().ainvoke({"out": {}})
        assert _AMBIENT_SEEN[-1] is None
        assert model_overrides_from_context(_AMBIENT_SEEN[-1]) == {}

    def setup_method(self):
        self.logger = logging.getLogger(f"{__name__}.{self.__class__.__name__}")

    def test_none_returns_empty(self):
        """Runtime.context is None when no context was passed (ADR-0033)."""
        assert model_overrides_from_context(None) == {}

    def test_instance_returns_overrides(self):
        """A KleaRunContext is read by attribute."""
        ctx = KleaRunContext(model_overrides=_overrides())
        assert model_overrides_from_context(ctx) == _overrides()

    def test_default_instance_returns_empty(self):
        """A context with no overrides yields {}."""
        assert model_overrides_from_context(KleaRunContext()) == {}

    def test_plain_dict_returns_overrides(self):
        """A raw dict (e.g. from a test harness) is read by key."""
        assert model_overrides_from_context({"model_overrides": _overrides()}) == (
            _overrides()
        )

    def test_plain_dict_without_key_returns_empty(self):
        """A raw dict without the conventional key yields {}."""
        assert model_overrides_from_context({}) == {}

    def test_subclass_returns_overrides(self):
        """An app subclass keeps the conventional key readable."""
        ctx = AppRunContext(model_overrides=_overrides(), user_locale="hi")
        assert model_overrides_from_context(ctx) == _overrides()
