#!/usr/bin/env python3
"""
Tests for the RAG InitRAGState node.

File: tests/test_init_rag.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail dot com>
"""

import logging

import pytest
from klea_rag.nodes.init_rag import InitRAGState
from klea_rag.schemas import RAGState


@pytest.mark.asyncio
async def test_init_preserves_session_allowed_dirs(monkeypatch):
    """Session-approved directories are not cleared by the per-run reset."""
    node = InitRAGState(logging.getLogger("test"), "Initializing")
    monkeypatch.setattr(node, "write_custom_stream", lambda ev: None)

    update = await node.execute(
        RAGState(allowed_dirs=["/tmp/approved"], allowed_files=["/tmp/approved/.env"])
    )

    assert "allowed_dirs" not in update
    assert "allowed_files" not in update
