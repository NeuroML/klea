#!/usr/bin/env python3
"""
Main API script

File: rag_pkg/klea_rag/api/main.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail DOT com>
"""

from klea_utils.api.app import make_app
from klea_utils.api.context import create_context_router
from klea_utils.api.health import create_health_router
from klea_utils.api.messages import create_messages_router
from klea_utils.api.models import create_models_router
from klea_utils.api.sessions import create_sessions_router

from klea_rag.api.chat import create_chat_router
from klea_rag.rag import RAG


def _create_rag() -> RAG:
    return RAG(checkpoint="sqlite")


app = make_app(
    graph_factory=_create_rag,
    title="Klea RAG API",
    version="0.2.0",
    routers=[
        create_chat_router(),
        create_context_router(),
        create_health_router(),
        create_messages_router(),
        create_models_router(),
        create_sessions_router(),
    ],
)
