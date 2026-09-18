#!/usr/bin/env python3
"""
Shared MCP tool registration helpers.

File: klea_utils/mcp/registry.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail DOT com>
"""

import functools
import inspect
import logging
from types import ModuleType
from typing import Any

from fastmcp import FastMCP
from fastmcp.server.middleware import CallNext, Middleware, MiddlewareContext
from mcp import types as mt
from mcp.types import ToolAnnotations

from klea_utils.mcp.privilege import (
    ALLOW_ROOT_ENV_VAR,
    allow_root_tools,
    root_denial_message,
    running_as_root,
    warn_if_root,
)
from klea_utils.mcp.schemas import ToolInfo
from klea_utils.mcp.tool_result import to_result

logger = logging.getLogger(__name__)


def _schema_declares_null(prop: dict[str, Any]) -> bool:
    """Return whether a JSON-schema property explicitly allows ``null``."""
    if prop.get("type") == "null":
        return True
    return any(
        isinstance(option, dict) and option.get("type") == "null"
        for option in prop.get("anyOf", []) or []
    )


def _strip_null_optionals(schema: dict[str, Any] | None, arguments: dict[str, Any]):
    """Delete ``null`` values for non-nullable, non-required arguments.

    Weak models frequently emit explicit ``null`` for optional tool arguments
    (e.g. ``max_chars: null``), which Pydantic rejects for a non-Optional
    field.  Dropping such a key before validation lets the function's own
    default apply - the same as omitting it.  Arguments whose schema allows
    ``null`` (e.g. ``limit: int | None``) or that are required are untouched.

    :param schema: The tool's JSON input schema (``Tool.parameters``).
    :param arguments: The request arguments, mutated in place.
    """
    properties = (schema or {}).get("properties", {}) or {}
    required = set((schema or {}).get("required", []) or [])
    for name, value in list(arguments.items()):
        if value is not None or name in required:
            continue
        prop = properties.get(name)
        if isinstance(prop, dict) and not _schema_declares_null(prop):
            del arguments[name]


class _DropNullOptionalsMiddleware(Middleware):
    """Treat an explicit ``null`` for a non-nullable optional as its default.

    Runs before argument validation, so ``{"max_chars": null}`` becomes an
    omitted argument and the tool's default is used, rather than failing with
    a validation error.  See :func:`_strip_null_optionals`.
    """

    async def on_call_tool(
        self,
        context: MiddlewareContext[mt.CallToolRequestParams],
        call_next: CallNext[mt.CallToolRequestParams, Any],
    ) -> Any:
        message = context.message
        if message.arguments and context.fastmcp_context is not None:
            tool = await context.fastmcp_context.fastmcp.get_tool(message.name)
            if tool is not None:
                _strip_null_optionals(tool.parameters, message.arguments)
        return await call_next(context)


def _wrap_with_root_guard(fn):
    """Wrap a tool so it refuses to run as root unless explicitly allowed.

    The guard runs at call time (the process uid is fixed, but the override
    env var is read per call).  ``functools.wraps`` preserves the signature,
    docstring and annotations so fastmcp still derives the tool schema and
    injects ``Context``; the wrapper also tolerates synchronous tools.

    :param fn: The registered tool function.
    :returns: An async wrapper that applies the root guard then calls *fn*.
    """

    @functools.wraps(fn)
    async def _guarded(*args: Any, **kwargs: Any) -> Any:
        if running_as_root() and not allow_root_tools():
            logger.warning(
                "Refusing to run tool %s as root (uid 0); set %s=1 to override",
                fn.__name__,
                ALLOW_ROOT_ENV_VAR,
            )
            return to_result({"error": root_denial_message()})
        result = fn(*args, **kwargs)
        if inspect.isawaitable(result):
            result = await result
        return result

    return _guarded


def register_tools(mcp: FastMCP, modules: list[ModuleType]):
    """Register tools from the given modules.

    A function is registered as a tool when it is decorated with
    :func:`tool_meta` (which attaches ``ToolInfo`` metadata).  The function
    name is used as the tool name.  Helper functions in the same module
    that are not decorated are ignored (logged at debug level, so a
    forgotten decoration is easy to spot).  Only functions *defined* in the
    given module are registered, so an imported decorated function is not
    picked up accidentally.

    Also installs :class:`_DropNullOptionalsMiddleware`, which tolerates weak
    models sending explicit ``null`` for optional arguments (treated as
    "use the default").

    :param mcp: FastMCP server to register the tools on.
    :param modules: list of modules with tool function definitions

    """
    warn_if_root(logger)
    mcp.add_middleware(_DropNullOptionalsMiddleware())
    for module in modules:
        for fname, fn in inspect.getmembers(module, inspect.isfunction):
            if fn.__module__ != module.__name__:
                # Imported function; not a registration candidate.
                continue
            if not hasattr(fn, "_tool_meta"):
                logger.debug(f"Skipping function without ToolInfo metadata: {fname}")
                continue

            metadata: ToolInfo = fn._tool_meta

            kwargs: dict[str, Any] = {}

            # Only pass an explicit description when ToolInfo
            # provides one.  Otherwise let fastmcp derive the
            # LLM-facing description from the docstring's opening
            # text block (klea's docstring-first convention).
            # Passing the raw docstring here would dump the whole
            # Args/Returns prose into the tool description,
            # duplicating parameter text that the client also shows
            # from the schema.
            #
            # Docstring conventions (summary + Use when / Do not
            # use for bullets + one example, ~100-250 tokens) are
            # documented in docs/concepts/mcp.rst, "Tool
            # description length and style".
            if metadata.description is not None:
                kwargs["description"] = metadata.description
            if metadata.title is not None:
                kwargs["title"] = metadata.title
            if metadata.tags is not None:
                kwargs["tags"] = metadata.tags
            if metadata.meta is not None or metadata.checkpaths is not None:
                # Fold checkpaths into the meta dict so it travels to clients
                # on the MCP Tool's _meta field; the tool caller node reads it
                # from there to gate path arguments before calling the tool.
                tool_meta_dict = dict(metadata.meta or {})
                if metadata.checkpaths is not None:
                    tool_meta_dict["checkpaths"] = list(metadata.checkpaths)
                kwargs["meta"] = tool_meta_dict

            # Fold the standard MCP ToolAnnotations hints.  Only hints that
            # are explicitly declared (non-None) are expressed; a tool with
            # no hints carries no annotations object.
            annotations_kwargs: dict[str, Any] = {}
            if metadata.read_only is not None:
                annotations_kwargs["readOnlyHint"] = metadata.read_only
            if metadata.destructive is not None:
                annotations_kwargs["destructiveHint"] = metadata.destructive
            if metadata.idempotent is not None:
                annotations_kwargs["idempotentHint"] = metadata.idempotent
            if metadata.open_world is not None:
                annotations_kwargs["openWorldHint"] = metadata.open_world
            if annotations_kwargs:
                kwargs["annotations"] = ToolAnnotations(**annotations_kwargs)

            mcp.tool(_wrap_with_root_guard(fn), **kwargs)
            logger.debug(f"Registered MCP tool: {fname}")


def tool_meta(metadata: ToolInfo):
    """Decorator that attaches :class:`ToolInfo` metadata to a tool function.

    Usage::

        @tool_meta(ToolInfo(tags={"bundled", "web"}))
        async def web_fetch(ctx: Context, url: str, ...):
            ...

    The metadata is read by :func:`register_tools` when the function is
    registered on a FastMCP server (it sets ``description``, ``title``,
    ``tags``, and ``meta`` on the tool if provided).  A function is only
    registered as a tool when it carries this decoration; the function name
    is used as the tool name.

    :param metadata: :class:`ToolInfo` to attach to the decorated function.
    :returns: The decorated function, unchanged, with ``_tool_meta`` set.
    """

    def wrapper(fn):
        fn._tool_meta = metadata
        return fn

    return wrapper
