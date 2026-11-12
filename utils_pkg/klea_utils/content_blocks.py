#!/usr/bin/env python3
"""
Shared content-block types for streamed node output.

See ``devdocs/system/streams.md``.  A node's streamed ``display`` may be
rendered either as plain Markdown or as structured blocks; these models
define that structured form.  They live here, in a neutral module, so both
the node/node-stream layer (``klea_utils/nodes/abstract.py``) and the
frontends (``klea_utils/ui/state.py``) can import them without a cycle.

File: klea_utils/content_blocks.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail DOT com>
"""

from __future__ import annotations

import logging
from typing import Annotated, Literal

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


class Item(BaseModel):
    """A single entry in an ``items`` block."""

    text: str = Field(description="Item text")
    # Plain ``str`` (never a ``Literal``): an unknown role must never fail
    # validation, so new roles can be added without breaking older readers.
    role: str = Field(default="plain", description="Item role")


class TextBlock(BaseModel):
    """A block of free text, rendered as Markdown or verbatim."""

    kind: Literal["text"] = "text"
    text: str = Field(description="Block text")
    format: Literal["markdown", "plain"] = Field(
        default="markdown", description="Text rendering format"
    )


class ItemsBlock(BaseModel):
    """An ordered list of items."""

    kind: Literal["items"] = "items"
    items: list[Item] = Field(default_factory=list, description="Block items")
    layout: Literal["spaced", "compact"] = Field(
        default="spaced",
        description="Item arrangement hint (closed set; unknown values render as spaced)",
    )


class FieldsBlock(BaseModel):
    """A mapping of field names to string values."""

    kind: Literal["fields"] = "fields"
    fields: dict[str, str] = Field(default_factory=dict, description="Named fields")


#: Discriminated union of the structured content blocks, keyed on ``kind``.
ContentBlock = Annotated[
    TextBlock | ItemsBlock | FieldsBlock, Field(discriminator="kind")
]
