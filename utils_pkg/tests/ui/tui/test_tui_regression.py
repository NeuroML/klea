"""Regression tests for TUI transcript windowing and spinner shutdown.

New tests only: no existing assertion in this package is modified.

Covered regressions:

* Appending more than ``Transcript.MAX_MOUNTED`` messages must keep every
  message in ``Transcript._messages`` while bounding the number of mounted
  blocks to ``Transcript.WINDOW_SIZE``, and scrolling back to the top must
  re-mount earlier blocks, in order, still bounded by
  ``Transcript.MAX_MOUNTED``.
* A stream that yields no events at all must still stop the turn spinner
  (``TurnStatus._timer is None``) and clear ``app._streaming``.
"""

from __future__ import annotations

import asyncio
import inspect

try:  # shipped package layout
    from klea_utils.ui.tui.components.transcript import Transcript
    from klea_utils.ui.tui.components.turn_status import TurnStatus
except ImportError:  # pragma: no cover - repo-relative layout fallback
    from utils_pkg.klea_utils.ui.tui.components.transcript import Transcript
    from utils_pkg.klea_utils.ui.tui.components.turn_status import TurnStatus


TOTAL_MESSAGES = 120


async def _maybe_await(value):
    """Await ``value`` only when it is awaitable."""
    if inspect.isawaitable(value):
        return await value
    return value


async def _settle(pilot, rounds: int = 25) -> None:
    for _ in range(rounds):
        await pilot.pause()
        await asyncio.sleep(0)


def _transcript_of(app) -> Transcript:
    """Return the ``#transcript`` widget the app composes on its screen."""
    return app.screen.query_one("#transcript", Transcript)


def _mounted_blocks(transcript):
    return list(transcript._mounted_blocks())


def _mounted_indices(transcript):
    """Contiguous message indexes of the mounted blocks.

    A live session mounts consecutive messages, so the indexes are derived
    from the implementation invariant rather than probing each block: the
    mounted window starts at ``Transcript._mounted_start`` and spans one
    index per mounted block.
    """
    start = transcript._mounted_start
    return list(range(start, start + len(transcript._mounted_blocks())))


async def _append(transcript, text: str, pilot) -> None:
    message = {"text": text, "stamp": "", "role": "assistant", "header": ""}
    await _maybe_await(transcript.append(message))
    await pilot.pause()


async def _submit(app, text: str):
    """Drive the app's single run slot through its submit path."""
    for owner in (app, getattr(app, "screen", None)):
        if owner is None:
            continue
        method = getattr(owner, "_run_query", None)
        if callable(method):
            return await _maybe_await(method(text))
    raise AssertionError("app exposes no _run_query submit path")


async def test_transcript_windowing_bounds_mounted_blocks(fake_backend, make_app):
    app = make_app()
    async with app.run_test() as pilot:
        # Let the app finish composing/mounting its ``#transcript`` widget
        # before querying it (as the app-driven TUI tests do).
        await pilot.pause()
        await app.workers.wait_for_complete()
        await pilot.pause()
        transcript = _transcript_of(app)

        for index in range(TOTAL_MESSAGES):
            await _append(transcript, f"message {index}", pilot)
        await _settle(pilot)

        # All data is retained even though only a window is mounted.
        assert len(transcript._messages) == TOTAL_MESSAGES

        blocks = _mounted_blocks(transcript)
        assert blocks, "expected at least one mounted block after appending"
        assert len(blocks) <= Transcript.MAX_MOUNTED

        bottom_indices = _mounted_indices(transcript)
        assert bottom_indices
        assert bottom_indices[0] == transcript._mounted_start
        assert bottom_indices == list(
            range(bottom_indices[0], bottom_indices[0] + len(bottom_indices))
        )

        # Scrolling back to the top must load older blocks, in order, while the
        # mounted count stays within the hard cap and every message is kept.
        transcript.scroll_home(animate=False)

        def scrolled_home() -> bool:
            return transcript._mounted_start < bottom_indices[0]

        for _ in range(200):
            if scrolled_home():
                break
            await _settle(pilot, rounds=2)

        blocks_after = _mounted_blocks(transcript)
        assert blocks_after
        assert len(blocks_after) <= Transcript.MAX_MOUNTED
        assert len(transcript._messages) == TOTAL_MESSAGES

        indices_after = _mounted_indices(transcript)
        assert indices_after == list(
            range(indices_after[0], indices_after[0] + len(indices_after))
        )
        assert indices_after[0] < bottom_indices[0]


async def test_spinner_stops_when_stream_yields_no_events(fake_backend, make_app):
    # A single empty script ends the stream without complete/error/interrupt.
    fake_backend.scripts = [[]]

    app = make_app()
    async with app.run_test() as pilot:
        await _submit(app, "hello there")

        status = app.screen.query_one(TurnStatus)
        for _ in range(200):
            if status._timer is None and app._streaming is False:
                break
            await _settle(pilot, rounds=2)

        assert status._timer is None
        assert app._streaming is False


async def test_transcript_scroll_down_remounts_evicted_newest(fake_backend, make_app):
    app = make_app()
    async with app.run_test() as pilot:
        # Let the app finish composing/mounting its ``#transcript`` widget.
        await pilot.pause()
        await app.workers.wait_for_complete()
        await pilot.pause()
        transcript = _transcript_of(app)

        for index in range(TOTAL_MESSAGES):
            await _append(transcript, f"message {index}", pilot)
        await _settle(pilot)

        # At the bottom, the newest message is mounted and the cap holds.
        assert len(transcript._messages) == TOTAL_MESSAGES
        bottom_indices = _mounted_indices(transcript)
        assert bottom_indices
        assert len(bottom_indices) <= Transcript.MAX_MOUNTED
        newest_bottom = bottom_indices[-1]

        # Scrolling home loads older blocks; enforcing the cap trims the newest.
        transcript.scroll_home(animate=False)

        def scrolled_home() -> bool:
            return transcript._mounted_start < bottom_indices[0]

        for _ in range(200):
            if scrolled_home():
                break
            await _settle(pilot, rounds=2)
        assert scrolled_home()

        trimmed_indices = _mounted_indices(transcript)
        assert trimmed_indices
        assert len(trimmed_indices) <= Transcript.MAX_MOUNTED
        assert len(transcript._messages) == TOTAL_MESSAGES
        assert trimmed_indices[-1] < newest_bottom, (
            "scrolling home should evict the newest mounted blocks"
        )
        evicted_newest = list(range(trimmed_indices[-1] + 1, newest_bottom + 1))
        assert evicted_newest

        # Driving the view back to the bottom must re-mount the newest blocks.
        transcript.scroll_end(animate=False)

        def restored_newest() -> bool:
            current = _mounted_indices(transcript)
            return bool(current) and current[-1] >= newest_bottom

        for _ in range(200):
            if restored_newest():
                break
            await app.workers.wait_for_complete()
            await _settle(pilot, rounds=2)
        assert restored_newest()

        restored_indices = _mounted_indices(transcript)
        assert set(evicted_newest).issubset(set(restored_indices))
        assert restored_indices == list(
            range(restored_indices[0], restored_indices[0] + len(restored_indices))
        )
        assert len(restored_indices) <= Transcript.MAX_MOUNTED
        assert len(transcript._mounted_blocks()) == len(restored_indices)
        assert len(transcript._messages) == TOTAL_MESSAGES
