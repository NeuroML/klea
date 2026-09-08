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


class TestModelOverridesFromContext:
    """The read helper normalizes every context shape to `{}` or the slice."""

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
