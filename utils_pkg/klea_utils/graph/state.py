#!/usr/bin/env python3
"""
Shared base state schema for LangGraph orchestrators.

File: klea_utils/graph/state.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail DOT com>
"""

from typing import Annotated

from fastmcp.client.client import CallToolResult
from langchain_core.messages import AnyMessage
from pydantic import BaseModel, Field

from klea_utils.graph.reducers import add_token_usage
from klea_utils.graph.schemas import TokenUsage
from klea_utils.mcp.schemas import ToolCallSchema


class BaseGraphSchema(BaseModel):
    """Base state shared by all Klea LangGraph orchestrators.

    Holds the fields the shared ``klea_utils`` nodes and the
    ``BaseLangGraph`` streaming layer rely on.  Applications subclass this
    and add their own fields -- the agent adds ``mode``/``plan``/... and RAG
    adds ``query_domains``/``reference_material``/... -- so the implicit
    cross-app contract has one home and a new shared field (e.g. the tool
    access level, ADR-0037) is available to every app without duplication
    (ADR-0032).

    ``usage_metrics`` uses the :func:`add_token_usage` reducer so parallel
    nodes can update it safely.  LangGraph resolves inherited ``Annotated``
    channels because it reads annotations with
    ``get_type_hints(..., include_extras=True)`` across the MRO.
    """

    query: str = ""
    messages: list[AnyMessage] = Field(default_factory=list)
    guard_decision: str = "safe"
    context_summary: str = ""
    summarised_till: int = 0
    message_for_user: str = ""
    tool_calls: list[ToolCallSchema] = Field(default_factory=list)
    tool_results: list[CallToolResult] = Field(default_factory=list)
    usage_metrics: Annotated[TokenUsage, add_token_usage] = Field(
        default_factory=TokenUsage
    )
