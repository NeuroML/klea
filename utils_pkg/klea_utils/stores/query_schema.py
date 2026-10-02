#!/usr/bin/env python3
"""
Strict-safe retrieval-query schema builder.

Builds the RAG query generator's structured-output schema from the domain's
configured filter fields, so each allowed filter is a real, typed, optional
field instead of a dynamic-key ``filters`` object.  A dynamic-key object
becomes a JSON object with ``additionalProperties``, which providers' strict
structured-output modes close to ``{}`` (ADR-0044) - the model then cannot
express any constraint at all.  One typed field per configured filter avoids
that and restricts the model to the deployment's declared field names.

Field type follows :attr:`FilterFieldInfo.value_type`:

* ``string`` -> ``str`` or ``list[str]`` (a list is an ``$in`` constraint);
* ``int`` -> ``int``, ``list[int]``, or an :class:`IntRange`
  (``{"gte": ..., "lte": ...}``; equality is a bare value);
* ``float`` -> ``float``, ``list[float]``, or a :class:`FloatRange`;
* ``list`` -> ``list[str]`` (element membership).

Every filter field is optional (omitted when the question states no
constraint).  A bare value is equality; a ``*Range`` value carries
``eq``/``gte``/``lte`` bounds.

The built schema is:
``{search_query: str, <filter field>: <type> | None, ...}``.

File: klea_utils/stores/query_schema.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail DOT com>
"""

import logging
from typing import Any

from pydantic import BaseModel, create_model

from klea_utils.stores.config import FilterFieldInfo

logger = logging.getLogger(__name__)


class IntRange(BaseModel):
    # Range/equality bounds for an integer filter field, translated to the
    # ``$eq``/``$gte``/``$lte`` DSL operators by the node.
    eq: int | None = None
    gte: int | None = None
    lte: int | None = None


class FloatRange(BaseModel):
    # Range/equality bounds for a float filter field.
    eq: float | None = None
    gte: float | None = None
    lte: float | None = None


#: Python type per configured ``value_type`` (scalars, lists, and ranges).
_FIELD_TYPES: dict[str, Any] = {
    "string": str | list[str],
    "int": int | list[int] | IntRange,
    "float": float | list[float] | FloatRange,
    "list": list[str],
}

#: The always-present field carrying the free-text search query.
_QUERY_FIELD = "search_query"

#: Process-local cache of built schemas, keyed by the field signature.
_SCHEMA_CACHE: dict[tuple[tuple[str, str], ...], type[BaseModel]] = {}


def _signature(fields: list[FilterFieldInfo]) -> tuple[tuple[str, str], ...]:
    """Return a stable cache key for a filter-field set."""
    return tuple(sorted((field.name, field.value_type) for field in fields))


def build_retrieval_query_schema(
    fields: list[FilterFieldInfo],
) -> type[BaseModel]:
    """Build the query generator's strict-safe structured-output schema.

    :param fields: The allowed filter fields for the run's domains (already
        resolved by the node).
    :returns: A pydantic model with ``search_query`` and one optional typed
        field per valid, uniquely-named configured filter.
    """
    key = _signature(fields)
    cached = _SCHEMA_CACHE.get(key)
    if cached is not None:
        return cached

    definitions: dict[str, Any] = {_QUERY_FIELD: (str, "")}
    for field in fields:
        if field.name == _QUERY_FIELD:
            logger.warning(
                f"Filter field {field.name!r} shadows the query field; skipped"
            )
            continue
        if not field.name.isidentifier():
            logger.warning(
                f"Filter field {field.name!r} is not a valid identifier; skipped"
            )
            continue
        python_type: Any = _FIELD_TYPES.get(field.value_type, str)
        definitions[field.name] = (python_type | None, None)

    output = create_model("RetrievalQueryOutput", **definitions)
    _SCHEMA_CACHE[key] = output
    logger.debug(
        f"built retrieval-query schema\n{len(fields) = }\n{list(definitions) = }"
    )
    return output
