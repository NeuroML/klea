#!/usr/bin/env python3
"""
Strict-safe tool-call schema builder for the tools picker.

Builds the picker's structured-output schema dynamically from the disclosed
tools, so each tool's parameters become real, typed fields instead of a
free-form ``args`` object.  A free-form object is *closed* by every
provider's strict structured-output mode (``additionalProperties: false``),
which silently forces ``args`` to ``{}``; a discriminated union of per-tool
call models avoids that and enforces each tool's required parameters.

The generated schema is:

* ``ToolPickerOutput`` - ``{tool_calls: list[ToolCall]}``
* ``ToolCall`` - ``{step, reason, call: <union>}`` (nested)
* one ``<tool>_call`` model per tool - ``{tool: Literal["<name>"], <params>}``
* a ``NoTool`` branch - ``{tool: Literal["no_tool"], reason}`` for the
  "no suggested tool can carry out this step" signal.

Type mapping (strict-safe): ``string``/``integer``/``number``/``boolean``
map directly; ``anyOf`` (including ``T | null``) maps to a union; arrays of
supported types map to ``list[...]``; object/unknown parameters fall back to
a JSON-encoded ``str`` (documented).  No generated field is an open object.

File: klea_utils/mcp/call_schema.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail DOT com>
"""

import json
import keyword
import logging
from collections.abc import Mapping
from functools import reduce
from operator import or_
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, create_model

from klea_utils.mcp.schemas import ToolInfo

logger = logging.getLogger(__name__)

#: Discriminator tag for the "no suggested tool fits" branch.  ``_update_state``
#: maps it to the picker's empty-tool failure signal.
NO_TOOL_TAG = "no_tool"

#: Suffix appended to a parameter description when its value is JSON-encoded
#: because its schema type is not directly representable (e.g. an object).
_JSON_ENCODED_NOTE = " (JSON-encoded value)"

#: Process-local cache of built schemas, keyed by the canonical tool signature.
_SCHEMA_CACHE: dict[str, type[BaseModel]] = {}


def _is_identifier(name: str) -> bool:
    """Return whether *name* is a valid, non-keyword Python identifier."""
    return name.isidentifier() and not keyword.iskeyword(name)


def _sanitize(name: str) -> str:
    """Return a safe model/field name for an arbitrary tool/param name."""
    safe = "".join(ch if (ch.isalnum() or ch == "_") else "_" for ch in name)
    if not safe:
        safe = "tool"
    if safe[0].isdigit():
        safe = f"_{safe}"
    if keyword.iskeyword(safe):
        safe = f"{safe}_"
    return safe


def _example_value(schema: dict[str, Any]) -> Any:
    """Return a placeholder example value for a JSON-schema fragment."""
    if not isinstance(schema, dict):
        return "text"
    examples = schema.get("examples")
    if isinstance(examples, list) and examples:
        return examples[0]
    enum = schema.get("enum")
    if isinstance(enum, list) and enum:
        return enum[0]
    if "anyOf" in schema:
        non_null = [b for b in schema["anyOf"] if b.get("type") != "null"]
        return _example_value(non_null[0]) if non_null else None
    match schema.get("type"):
        case "string":
            return "text"
        case "integer":
            return 0
        case "number":
            return 0.0
        case "boolean":
            return True
        case "array":
            return [_example_value(schema.get("items") or {})]
        case "object":
            return {}
        case _:
            return "text"


def _py_type(schema: dict[str, Any] | None) -> Any:
    """Map a JSON-schema fragment to a Python type, or ``None`` if unsupported.

    ``None`` means "not directly representable" (object, unknown); the caller
    falls back to a JSON-encoded string.
    """
    if not isinstance(schema, dict):
        return None
    enum = schema.get("enum")
    if isinstance(enum, list) and enum and all(isinstance(v, str) for v in enum):
        return Literal[tuple(enum)]  # ty: ignore[invalid-type-form]
    if "anyOf" in schema:
        return _py_type_union(schema["anyOf"])
    match schema.get("type"):
        case "string":
            return str
        case "integer":
            return int
        case "number":
            return float
        case "boolean":
            return bool
        case "null":
            return type(None)
        case "array":
            item = _py_type(schema.get("items") or {})
            return list[item] if item is not None else None
        case _:
            return None


def _py_type_union(branches: list[dict[str, Any]]) -> Any:
    """Combine ``anyOf`` branches into a Python union, allowing ``null``."""
    types: list[type] = []
    optional = False
    for branch in branches:
        branch_type = _py_type(branch)
        if branch_type is type(None):
            optional = True
            continue
        if branch_type is None:
            return None
        types.append(branch_type)
    if not types:
        return None
    combined = types[0]
    for extra in types[1:]:
        combined = combined | extra
    if optional:
        combined = combined | None
    return combined


def _field(name: str, prop: dict[str, Any], required: bool) -> tuple[Any, Any]:
    """Return a ``create_model`` field tuple for one tool parameter."""
    py_type = _py_type(prop)
    description = (prop.get("description") or "").strip()
    if py_type is None:
        py_type = str
        description = f"{description}{_JSON_ENCODED_NOTE}".strip()
    alias = name if _is_identifier(name) else _sanitize(name)
    use_alias = None if alias == name else name
    if required:
        default: Any = ...
    elif prop.get("default") is not None:
        default = prop["default"]
    else:
        py_type = py_type | None
        default = None
    return (
        py_type,
        Field(default=default, description=description or None, alias=use_alias),
    )


def _tool_model(name: str, info: ToolInfo) -> type[BaseModel]:
    """Build the call model for one tool from its input schema."""
    schema = info.input_schema or {}
    properties = schema.get("properties") or {}
    required = set(schema.get("required") or [])

    fields: dict[str, Any] = {"tool": (Literal[name], ...)}  # ty: ignore[invalid-type-form]
    example: dict[str, Any] = {"tool": name}
    for param, prop in properties.items():
        field_name = _sanitize(param)
        fields[field_name] = _field(
            param, prop if isinstance(prop, dict) else {}, param in required
        )
        example[param] = _example_value(prop if isinstance(prop, dict) else {})

    config = ConfigDict(
        populate_by_name=True, json_schema_extra={"examples": [example]}
    )
    return create_model(f"{_sanitize(name)}_call", __config__=config, **fields)


def _signature(tools: Mapping[str, ToolInfo]) -> str:
    """Return a canonical, order-independent key for a disclosed tool set."""
    return json.dumps(
        [[name, tools[name].input_schema or {}] for name in sorted(tools)],
        sort_keys=True,
        default=str,
    )


def build_tool_call_schema(tools: Mapping[str, ToolInfo]) -> type[BaseModel]:
    """Build the picker's structured-output schema for a tool set.

    The schema is a nested discriminated union over the given tools plus a
    ``NoTool`` branch.  Tool order is deterministic (sorted by name) so the
    serialized schema - and therefore the prompt's ``Output schema`` block -
    is stable for a given tool set (prompt caching).

    :param tools: The disclosed tools (``{name: ToolInfo}``), already filtered
        by access level and, for RAG, by the run's domains.
    :returns: A pydantic model class with a ``tool_calls`` field.
    """
    key = _signature(tools)
    cached = _SCHEMA_CACHE.get(key)
    if cached is not None:
        return cached

    call_models = [_tool_model(name, tools[name]) for name in sorted(tools)]
    no_tool = create_model(
        "NoTool",
        tool=(Literal[NO_TOOL_TAG], ...),  # ty: ignore[invalid-type-form]
        reason=(str, ...),
    )
    call_types = reduce(or_, (*call_models, no_tool))
    call_union = Annotated[
        call_types,  # ty: ignore[invalid-type-form]
        Field(discriminator="tool"),
    ]
    wrapper = create_model(
        "ToolCall",
        step=(int, ...),
        reason=(str, ""),
        # ``paths`` is the picker's declared filesystem paths for the call
        # (the ``expected`` permission tier).  Optional with an empty default
        # so a weak model omitting it cannot fail structured output; the
        # prompt asks for it on every call.
        paths=(list[str], Field(default_factory=list)),
        call=(call_union, ...),
    )
    output = create_model(
        "ToolPickerOutput",
        tool_calls=(list[wrapper], Field(default_factory=list)),  # ty: ignore[invalid-type-form]
    )
    logger.debug(
        f"built tool-call schema\n{len(call_models) = }\n"
        f"{sorted(tools) = }\n{output.__name__ = }"
    )
    _SCHEMA_CACHE[key] = output
    return output
