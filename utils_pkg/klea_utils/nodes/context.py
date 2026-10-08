#!/usr/bin/env python3
"""
Per-run node context.

A node's intermediate values (prompt, LLM instance, raw output, result,
token usage, ...) are local to a single ``execute`` invocation.  They live
in a :class:`NodeContext` built and passed by ``execute``, instead of on
the shared node instance - so two concurrent runs cannot clobber each
other's state.

The context deliberately does **not** carry the graph ``state``: that is
already an explicit argument to the hooks that need it, and keeping it out
avoids duplicating (and diverging from) the checkpointed state.

File: klea_utils/nodes/context.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail DOT com>
"""

from dataclasses import dataclass, field
from typing import Any, Generic, TypeVar

from fastmcp.client.client import CallToolResult
from langchain_core.prompt_values import PromptValue
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.runnables import Runnable, RunnableConfig
from pydantic import BaseModel

from klea_utils.graph.schemas import TokenUsage

TOutput = TypeVar("TOutput")


@dataclass
class NodeContext:
    """Base per-run node scratch space (no graph state).

    Subclasses add the fields a node family computes during execution.  The
    object is created by ``execute`` for a single run and passed to that
    run's hooks; it is never stored on the shared node instance.
    """


@dataclass
class LLMNodeContext(NodeContext, Generic[TOutput]):
    """Per-run scratch for an LLM node.

    Mirrors the values the LLM template computes and the streaming/inspection
    hooks read: the resolved schema, the prompt pipeline, the LLM invocation
    and its processed output.
    """

    #: Output schema resolved for this run (static or dynamic).
    output_schema: type[BaseModel] | None = None
    system_prompt: Any = None
    human_prompt: str | None = None
    template: ChatPromptTemplate | None = None
    variables: dict[str, Any] = field(default_factory=dict)
    prompt: PromptValue | None = None
    llm: Runnable | None = None
    config: RunnableConfig | None = None
    #: Raw LLM output and its processed/validated result.
    output: Any = None
    result: TOutput | None = None
    #: Validation feedback from the most recent rejected result.
    validation_feedback: str = ""
    #: State updates produced by ``_update_state`` and the final merged dict.
    state_updates: dict[str, Any] | None = None
    final_state: dict[str, Any] | None = None
    #: Token usage for this run.
    token_usage: TokenUsage | None = None


@dataclass
class ToolCallerContext(NodeContext):
    """Per-run scratch for the tool-caller node."""

    tool_results: list[CallToolResult] = field(default_factory=list)
    #: Per-result display flag, aligned with :attr:`tool_results`.
    display_flags: list[bool] = field(default_factory=list)
    #: Directories the user approved for the session while resolving a
    #: permission interrupt (``allow for session``); persisted to
    #: ``state.allowed_dirs`` by the node (ADR-0007 update 2026-10-08).
    allowed_session_dirs: list[str] = field(default_factory=list)
    #: Sensitive files approved for the session (``allow for session`` on a
    #: sensitive request); persisted to ``state.allowed_files``.
    allowed_session_files: list[str] = field(default_factory=list)
