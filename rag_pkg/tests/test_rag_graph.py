#!/usr/bin/env python3
"""
Structural test: the RAG graph is built with the ADR-0033 context schema.

Compiles the builder without a full setup (dummy models, no vector stores,
no MCP client) and asserts the LangGraph ``StateGraph`` was constructed with
``context_schema=KleaRunContext``, so per-run model overrides can reach the
nodes via ambient ``get_runtime()`` (ADR-0033).

File: tests/test_rag_graph.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail DOT com>
"""

import logging

import pytest
from klea_rag.config import AppConfig, GeneralConfig
from klea_rag.rag import RAG
from klea_utils.graph.context import KleaRunContext
from klea_utils.llm import LLMModel
from klea_utils.stores.config import RetrieverConfig
from langgraph.graph.state import StateGraph


@pytest.mark.asyncio
async def test_rag_graph_registers_context_schema(monkeypatch):
    """The RAG workflow is built with the shared KleaRunContext schema."""
    logger = logging.getLogger(f"{__name__}.test_rag_graph")
    rag = RAG(checkpoint="inmemory")
    # Dummy models / resources so ``_create_graph`` builds nodes without any
    # setup or LLM; compile is stubbed so the schema check needs no checkpointer.
    rag.llm_models = {
        "chat": LLMModel(instance=None, model_name=""),
        "tool_picker": LLMModel(instance=None, model_name=""),
        "guard": LLMModel(instance=None, model_name=""),
        "embedding": LLMModel(instance=None, model_name=""),
    }
    rag.retriever_config = RetrieverConfig(domains={})
    rag.stores = None
    rag.bm25_stores = None
    rag.mcp_client = None
    rag.mcp_tools = None
    rag.tools_info = {}
    rag.app_config = AppConfig(general=GeneralConfig(), domains={})
    rag.refusal_message = "Sorry. I cannot answer this query."
    rag.clarification_message = "Please reword your query."
    rag.max_refs_size = 5

    monkeypatch.setattr(StateGraph, "compile", lambda self, **kwargs: object())
    await rag._create_graph()
    assert rag.workflow is not None
    assert rag.workflow.context_schema is KleaRunContext
    logger.debug("RAG workflow context_schema = %s", rag.workflow.context_schema)


def test_embedding_role_is_locked():
    """The embedding model is not modifiable: stores are embedded once at
    startup, so a per-request override could never be honoured."""
    rag = RAG(checkpoint="inmemory")
    rag._setup_models()

    entry = rag.llm_models["embedding"]
    assert entry.modifiable is False
    # Chat roles stay user-configurable.
    assert rag.llm_models["chat"].modifiable is True
