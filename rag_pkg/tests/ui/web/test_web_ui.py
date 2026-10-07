#!/usr/bin/env python3
"""
Tests for the RAG NiceGUI page using NiceGUI's in-process user simulation.

The page is driven without a browser: ``nicegui.testing.user_simulation``
runs :func:`klea_rag.ui.web.page.setup_layout` in-process, while a patched
``httpx.AsyncClient`` routes the frontend's backend calls to a canned fake
backend, so no server or model is needed.  The fixtures live in
``tests/ui/web/conftest.py``.

File: tests/ui/web/test_web_ui.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail DOT com>
"""

from nicegui import ui


def _send(user, text: str) -> None:
    """Type *text* into the chat input and press Enter."""
    user.find(ui.textarea).type(text)
    user.find(ui.textarea).trigger("keydown.enter.exact.prevent")


async def test_rag_page_renders(rag_user):
    """The RAG page renders its chrome and clears the readiness banner."""
    await rag_user.open("/")
    await rag_user.should_see("Klea Test")
    await rag_user.should_see("Start a conversation")
    await rag_user.should_see("inspect")
    await rag_user.should_not_see("Backend is starting")


async def test_rag_has_no_agent_context_controls(rag_user):
    """The RAG page has no operating-mode or access-level selectors."""
    await rag_user.open("/")
    await rag_user.should_not_see("Mode:")
    await rag_user.should_not_see("Access:")


async def test_send_renders_the_answer(fake_backend, rag_user):
    """A completed turn shows the user message and the answer."""
    fake_backend.stream_events = [{"type": "complete", "message_for_user": "Hi there"}]
    await rag_user.open("/")
    await rag_user.should_not_see("Backend is starting")
    _send(rag_user, "Hello")
    await rag_user.should_see("Hello")
    await rag_user.should_see("Hi there", retries=50)

    body = fake_backend.stream_bodies()[-1]
    assert body["query"] == "Hello"
    assert "mode" not in body
    assert "access_level" not in body
