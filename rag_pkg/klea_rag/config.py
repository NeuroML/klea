#!/usr/bin/env python3
"""
Config schema for the app

File: rag_pkg/klea_rag/config.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail DOT com>
"""

from pathlib import Path
from typing import Any

from klea_utils.mcp.access import ToolAccessOverride
from klea_utils.mcp.server.config import BundledToolsConfig
from klea_utils.stores.config import PerDomainConfig as BasePerDomainConfig
from pydantic import BaseModel, Field, model_validator


class GeneralConfig(BaseModel):
    """General configuration.

    ``default_k``, ``k_max``, and ``k_inc`` are the graph-wide fallbacks
    applied to vector stores that do not define their own per-store values.
    ``k_max`` caps how many candidates each store fetches per retrieval pass
    and, once reached, pushes the evaluator loop to reformulate the query.
    ``max_refs_size`` is the character budget for the reference material
    actually fed to the answer LLM, independent of ``k``.
    """

    default_k: int = Field(default=5, ge=1)
    k_max: int = Field(default=10, ge=1)
    k_inc: int = Field(default=1, ge=1)
    # char budget for the reference material serialized into the LLM context
    # (see klea_utils.stores.utils.truncate_reference_material)
    max_refs_size: int = Field(default=20000, ge=1)
    # TODO: unused---what is this for?
    pre_prompt: str = ""
    non_domain_chat: bool = True
    fallback_to_training_data: bool = True
    fallback_warning: str = ""
    max_retrieval_attempts: int = Field(default=5, ge=1)
    max_rewrite_attempts: int = Field(default=1, ge=0)
    #: The shared bundled tools server is opt-in for the RAG: deployments
    #: wire in the common file/web/download tools explicitly rather than
    #: getting them by default (RAGs are domain specific, so which common
    #: tools make sense differs per deployment).
    bundled_tools: BundledToolsConfig = Field(
        default_factory=lambda: BundledToolsConfig(enabled=False)
    )
    #: Per-tool capability overrides (ADR-0037), applied over MCP annotations.
    #: The RAG is fixed at ``read_only`` (its state default); this only lets a
    #: deployment declare unannotated tools as read-only so they stay usable.
    tool_access: dict[str, ToolAccessOverride] = Field(
        default_factory=dict,
        description="Per-tool read_only/destructive overrides (ADR-0037)",
    )

    @model_validator(mode="after")
    def _validate_k(self) -> "GeneralConfig":
        if self.k_max < self.default_k:
            raise ValueError(
                f"k_max ({self.k_max}) must be >= default_k ({self.default_k})"
            )
        return self


class PerDomainConfig(BasePerDomainConfig):
    """Configuration for a single domain."""

    description: str
    mcp_servers: dict[str, Any] = Field(default_factory=dict)


class AppConfig(BaseModel):
    general: GeneralConfig
    providers: dict[str, dict[str, dict[str, Any]]] = Field(default_factory=dict)
    domains: dict[str, PerDomainConfig]


def write_config_template(output_dir: str | Path) -> Path:
    """Write a scaffold ``klea_rag.json`` into *output_dir*.

    The template is built from the schema defaults so every general
    option is present, plus a placeholder ``ExampleDomain`` showing the
    shape of a domain entry (description, vector store, BM25 store, MCP
    servers) for the user to edit.  Refuses to overwrite an existing
    file so a real config is never clobbered.

    :param output_dir: Directory to write the template into
    :returns: Path to the written template
    :raises FileExistsError: If the target file already exists
    """
    target = Path(output_dir) / "klea_rag.json"
    if target.exists():
        raise FileExistsError(
            f"Refusing to overwrite existing config: {target}. "
            "Use --profile <name> with a different name instead."
        )
    config = AppConfig(
        general=GeneralConfig(),
        domains={
            "ExampleDomain": PerDomainConfig(
                description="Documents related to your project",
                vector_stores=[
                    {"name": "my-docs", "path": "chroma:/path/to/my-vector-store"}
                ],
                bm25_stores=[
                    {"name": "my-docs-bm25", "path": "/path/to/my-bm25-corpus.pkl"}
                ],
                # Filter fields are declared per domain: the retrieval query
                # generator may only propose constraints on metadata fields
                # listed here (a store in the domain must store them).
                filter_fields=[
                    {
                        "name": "topic",
                        "description": (
                            "the section or topic a document belongs to; "
                            "use the exact topic name"
                        ),
                        "value_type": "string",
                    },
                    {
                        "name": "tags",
                        "description": "topic tags applied to a document",
                        "value_type": "list",
                    },
                ],
            )
        },
    )
    target.write_text(config.model_dump_json(exclude_none=True, indent=2) + "\n")
    return target
