#!/usr/bin/env python3
"""
Tests for the in-flight run registry.

File: utils_pkg/tests/test_active_runs.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail DOT com>
"""

import asyncio
import logging

from klea_utils.api.runs import ActiveRunRegistry

logger = logging.getLogger(__name__)


async def _idle() -> None:
    await asyncio.sleep(10)


def test_register_and_is_active() -> None:
    async def _run() -> None:
        registry = ActiveRunRegistry()
        task = asyncio.create_task(_idle())
        registry.register("t1", task)
        assert registry.is_active("t1")
        assert registry.get("t1") is task
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    asyncio.run(_run())


def test_is_active_false_when_empty() -> None:
    registry = ActiveRunRegistry()
    assert not registry.is_active("missing")
    assert registry.get("missing") is None


def test_done_callback_clears_entry() -> None:
    async def _run() -> None:
        registry = ActiveRunRegistry()
        task = asyncio.create_task(asyncio.sleep(0))
        registry.register("t1", task)
        await task
        # The done callback runs on the next loop turn.
        await asyncio.sleep(0)
        assert not registry.is_active("t1")
        assert registry.get("t1") is None

    asyncio.run(_run())


def test_cancel_active_returns_true_and_cancels() -> None:
    async def _run() -> None:
        registry = ActiveRunRegistry()
        task = asyncio.create_task(_idle())
        registry.register("t1", task)
        assert registry.cancel("t1") is True
        try:
            await task
        except asyncio.CancelledError:
            pass
        assert task.cancelled()
        await asyncio.sleep(0)
        assert not registry.is_active("t1")

    asyncio.run(_run())


def test_cancel_missing_is_noop() -> None:
    registry = ActiveRunRegistry()
    assert registry.cancel("missing") is False


def test_cancel_done_task_is_noop() -> None:
    async def _run() -> None:
        registry = ActiveRunRegistry()
        task = asyncio.create_task(asyncio.sleep(0))
        registry.register("t1", task)
        await task
        assert registry.cancel("t1") is False

    asyncio.run(_run())


def test_clear_ignores_live_task() -> None:
    async def _run() -> None:
        registry = ActiveRunRegistry()
        task = asyncio.create_task(_idle())
        registry.register("t1", task)
        registry.clear("t1")
        # A live task must not be evicted by an explicit clear.
        assert registry.is_active("t1")
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    asyncio.run(_run())


def test_clear_removes_done_task() -> None:
    async def _run() -> None:
        registry = ActiveRunRegistry()
        task = asyncio.create_task(asyncio.sleep(0))
        registry.register("t1", task)
        await task
        registry.clear("t1")
        assert registry.get("t1") is None

    asyncio.run(_run())


def test_distinct_threads_are_independent() -> None:
    async def _run() -> None:
        registry = ActiveRunRegistry()
        t1 = asyncio.create_task(_idle())
        t2 = asyncio.create_task(_idle())
        registry.register("a", t1)
        registry.register("b", t2)
        assert registry.is_active("a") and registry.is_active("b")
        assert registry.cancel("a") is True
        assert registry.is_active("b")
        for task in (t1, t2):
            task.cancel()
        for task in (t1, t2):
            try:
                await task
            except asyncio.CancelledError:
                pass

    asyncio.run(_run())
