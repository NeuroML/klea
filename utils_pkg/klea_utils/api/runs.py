#!/usr/bin/env python3
"""
Per-process registry of in-flight graph runs, keyed by checkpoint thread.

A LangGraph checkpoint is a log, not a mutex: two ``ainvoke`` calls on the
same ``thread_id`` run concurrently and the last writer wins.  Klea prevents
that by holding a handle to the asyncio task driving each thread, so a new
query can be rejected while a run is active and an active run can be
cancelled (``CancelledError`` reaches the node at its next ``await`` and
leaves the checkpoint resumable).

The registry is deliberately in-process: the server runs a single uvicorn
worker, and cancelling an ``asyncio.Task`` is only possible in the process
that owns it.  Multi-worker deployments would need sticky routing or a
distributed cancellation signal.

File: klea_utils/api/runs.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail DOT com>
"""

import asyncio
import logging

logger = logging.getLogger(__name__)


class ActiveRunRegistry:
    """Track the asyncio task currently driving each checkpoint thread.

    One entry per ``thread_id``: distinct chats stay concurrent, while a
    chat's thread is single-flight.  All methods are synchronous and safe
    to call from the event loop.
    """

    def __init__(self) -> None:
        """Initialise an empty registry."""
        self._runs: dict[str, asyncio.Task] = {}

    def register(self, thread_id: str, task: asyncio.Task) -> None:
        """Record *task* as the active run for *thread_id*.

        A ``done`` callback clears the entry so a task that finishes
        without an explicit :meth:`clear` (e.g. an unexpected exit) cannot
        leak.  Registration is expected to be gated by :meth:`is_active`
        at the call site.

        :param thread_id: The checkpoint thread identifier.
        :param task: The task driving the graph run.
        """
        self._runs[thread_id] = task
        task.add_done_callback(lambda _t, key=thread_id: self.clear(key))
        logger.debug(f"{thread_id = }\n{id(task) = }")

    def clear(self, thread_id: str) -> None:
        """Drop the entry for *thread_id* if present (idempotent).

        Only removes the entry when it is done or absent, so a late
        ``clear`` from a superseded task cannot evict a newer active run.

        :param thread_id: The checkpoint thread identifier.
        """
        existing = self._runs.get(thread_id)
        if existing is not None and not existing.done():
            logger.debug(
                f"clear() ignored for {thread_id = }: task still active "
                f"{id(existing) = }"
            )
            return
        self._runs.pop(thread_id, None)
        logger.debug(f"cleared {thread_id = }")

    def is_active(self, thread_id: str) -> bool:
        """Return whether a live (not done) run is registered for *thread_id*.

        :param thread_id: The checkpoint thread identifier.
        :returns: True when a run is currently in flight.
        """
        task = self._runs.get(thread_id)
        return task is not None and not task.done()

    def get(self, thread_id: str) -> asyncio.Task | None:
        """Return the task registered for *thread_id*, or ``None``.

        :param thread_id: The checkpoint thread identifier.
        :returns: The active task, if any (may be done).
        """
        return self._runs.get(thread_id)

    def cancel(self, thread_id: str) -> bool:
        """Cancel the active run for *thread_id* (idempotent).

        ``Task.cancel`` on an already-done task is a no-op, so a
        cancel/completion race is safe.

        :param thread_id: The checkpoint thread identifier.
        :returns: True when a live run was found and cancellation requested.
        """
        task = self._runs.get(thread_id)
        if task is None or task.done():
            logger.debug(f"cancel() no live run for {thread_id = }")
            return False
        logger.info(f"Cancelling active run for {thread_id = } (task {id(task)})")
        task.cancel()
        return True
