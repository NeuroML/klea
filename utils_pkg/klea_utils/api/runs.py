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

        A ``done`` callback clears the entry, but only when the stored task
        is still *this* task, so a late callback from a superseded run
        cannot evict a newer one.  Registration is expected to be gated by
        :meth:`is_active` at the call site.

        :param thread_id: The checkpoint thread identifier.
        :param task: The task driving the graph run.
        """
        self._runs[thread_id] = task

        def _on_done(finished: asyncio.Task, key: str = thread_id) -> None:
            if self._runs.get(key) is finished:
                self._runs.pop(key, None)
                logger.debug(f"done-callback cleared {key = }")

        task.add_done_callback(_on_done)
        logger.debug(f"{thread_id = }\n{id(task) = }")

    def clear(self, thread_id: str) -> None:
        """Drop the entry for *thread_id* if present (idempotent).

        Removes the entry unconditionally: the registering task calls this
        from its own ``finally`` while still running (so its task is not
        ``done``), and a superseded task's late clear is prevented by
        :meth:`register`'s done-callback identity check, not here.

        :param thread_id: The checkpoint thread identifier.
        """
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
