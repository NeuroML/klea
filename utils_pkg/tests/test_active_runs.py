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


def test_clear_removes_entry_even_while_task_running() -> None:
    async def _run() -> None:
        registry = ActiveRunRegistry()
        task = asyncio.create_task(_idle())
        registry.register("t1", task)
        # The registering task calls clear from its own finally while still
        # running, so a live task must be removable.
        registry.clear("t1")
        assert registry.get("t1") is None
        assert not registry.is_active("t1")
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    asyncio.run(_run())


def test_done_callback_does_not_evict_newer_run() -> None:
    async def _run() -> None:
        registry = ActiveRunRegistry()
        old = asyncio.create_task(_idle())
        registry.register("t1", old)
        new = asyncio.create_task(_idle())
        # A newer run supersedes the old entry before the old task finishes.
        registry.register("t1", new)
        old.cancel()
        try:
            await old
        except asyncio.CancelledError:
            pass
        await asyncio.sleep(0)
        # The old task's done-callback must not evict the newer run.
        assert registry.get("t1") is new
        new.cancel()
        try:
            await new
        except asyncio.CancelledError:
            pass

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
