#!/usr/bin/env python3
"""
Base class for LangGraph-based orchestrators

File: klea_utils/graph/base.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail DOT com>
"""

import json
import logging
import os
import sys
import time
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, Literal, cast, final

from fastmcp import Client
from fastmcp.mcp_config import MCPConfig
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.graph.state import CompiledStateGraph
from langgraph.stream import StreamTransformer
from langgraph.types import RunnableConfig
from mcp.types import Tool
from platformdirs import PlatformDirs
from pydantic import BaseModel, ConfigDict, Field, create_model

from klea_utils.graph.context import KleaRunContext
from klea_utils.llm import LLMModel, resolve_user_agent
from klea_utils.mcp.access import (
    AccessLevel,
    ToolAccessOverride,
    resolve_access_level,
    resolve_capability,
)
from klea_utils.mcp.privilege import warn_if_root
from klea_utils.mcp.schemas import ToolCallSchema, ToolInfo
from klea_utils.paths import get_config_dir, init_dir, resolve_app_config_path
from klea_utils.stores.config import RetrieverConfig
from klea_utils.stores.retrieval.bm25 import BM25RetrieverManager
from klea_utils.stores.retrieval.vs import VSRetriever
from klea_utils.tools import build_tool_description, clean_tool_meta

# Per-request model overrides are carried by the LangGraph Runtime context
# (KleaRunContext, ADR-0033), not a contextvar: the API layer builds the
# context from the sessions DB per invocation and the nodes read it via
# ambient ``get_runtime()``.  The old ``model_overrides_ctx`` contextvar
# is gone -- see ``klea_utils.graph.context``.


def _normalise_state_snapshot(state: Any) -> dict[str, Any]:
    """Normalize a checkpoint ``values`` snapshot into a plain dict.

    LangGraph state snapshots arrive either as a dict (TypedDict state)
    or as the pydantic state instance.  ``context_snapshot`` and the
    hydration endpoint (``klea_utils.api.context``) both need a plain
    dict, so the normalization lives here: one code path shared by the
    stream loop and the direct checkpoint read, guaranteeing the hook
    sees identical input wherever it is called.

    :param state: A checkpoint/superstep ``values`` snapshot.
    :returns: A plain dict view of *state*.
    """
    if not isinstance(state, dict) and hasattr(state, "model_dump"):
        return state.model_dump()
    return state


class _CustomChannelEnabler(StreamTransformer):
    """Enables the ``custom`` channel in LangGraph v3 event streams.

    Non-LLM nodes use ``get_stream_writer()`` to emit progress events on the
    custom channel.  LangGraph requires a ``StreamTransformer`` declaring
    ``required_stream_modes = ("custom",)`` for that channel to be enabled.
    This no-op transformer satisfies that requirement.
    """

    required_stream_modes = ("custom",)

    def __init__(self, scope=()):
        super().__init__(scope)

    def init(self):
        return {}

    def process(self, event):
        return True


class BaseLangGraph(ABC):
    """Abstract base class for LangGraph-based orchestrators.

    Provides common infrastructure for:
    - Configuration loading from env files
    - MCP client creation from JSON config
    - LLM model setup (delegated to subclasses)
    - LangGraph compilation and execution
    - Session checkpointing
    - Dual-stream logging

    Subclasses must implement:

    - ``_setup_models``: create ``self.llm_models``; the env schema is
      generated from its roles.
    - ``_create_graph``: build and compile the LangGraph.
    - ``config_class``: the Pydantic class for the JSON configuration.
    """

    #: Pydantic BaseSettings class for env loading.  Subclasses need not
    #: set this: it is generated at load time from ``self.llm_models`` (see
    #: :meth:`_build_env_class`).
    env_class: type[BaseModel] = BaseModel

    #: Pydantic BaseModel class for configuration loading.
    #: Subclasses must set this to their AppConfig class.
    config_class: type[BaseModel]

    #: Name of the environment variable that controls the env file path.
    env_var: str = "ENV_FILE"

    #: Default config file name if the environment variable is not set.
    env_file_default: str = "config.env"

    #: Prefix prepended to generated env var names (e.g. ``"KLEA_AGENT_"``).
    #: Each model role ``r`` becomes the env var ``<env_prefix>R_MODEL``.
    env_prefix: str = ""

    #: Default JSON config file name, used as the ``app_config_file`` env
    #: field default when none is set in the env file or process env.
    config_file_default: str = ""

    #: Logger name for this orchestrator, also used as the app name
    #: for ``platformdirs`` data/cache directories.
    graph_name: str = "BaseLangGraph"

    def __init__(
        self,
        logging_level: int = logging.INFO,
        checkpoint: str = "inmemory",
        log_file: bool = True,
    ):
        """Initialise the base orchestrator.

        :param logging_level: Logging level for the orchestrator.  The
            :data:`KLEA_LOG_LEVEL` environment variable (and the ``--debug``
            flag that sets it) takes precedence over this constructor
            argument: when it resolves to ``DEBUG``, full debug logging is
            enabled regardless of *logging_level*; otherwise *logging_level*
            is used (default ``INFO``).
        :param checkpoint: Checkpointer mode  ---  ``"inmemory"`` (volatile, default),
            ``"sqlite"`` (persistent via ``self.paths.user_data_dir``),
            or ``"none"`` (no checkpointing).  When set to ``"none"``, nodes
            that need conversation history receive ``memory=False``.
        :param log_file: When ``True`` (default), configure the process-wide
            root logger with a rotating file handler writing to
            ``{self.paths.user_data_dir}/{self.graph_name}.log``, alongside
            the checkpoints and session database.  Set to ``False`` in
            short-lived processes (e.g. tests) to avoid writing log files.
        """
        self.env_file = os.getenv(self.env_var, self.env_file_default)
        self.app_env: BaseModel

        # Graph-level default models per role.  Per-request model
        # overrides arrive via the LangGraph Runtime context (KleaRunContext,
        # ADR-0033) and do NOT change this dict.
        self.llm_models: dict[str, LLMModel] = {}

        self.tools_info: dict[str, dict[str, ToolInfo]] = {}
        self.domain_mcp_configs: dict[str, MCPConfig] = {}
        self.checkpointer_mode = checkpoint
        self.memory = checkpoint != "none"
        self.checkpointer: None | AsyncSqliteSaver | InMemorySaver = None

        self.paths = PlatformDirs(self.graph_name.lower())
        #: Klea identity sent to OpenAI-compatible endpoints (User-Agent).
        self.user_agent = resolve_user_agent(self.graph_name)

        self.config_dict: dict[str, Any]

        self.graph: CompiledStateGraph[Any, Any, Any, Any] | None = None

        #: Labels of nodes whose LLM token deltas should be forwarded as
        #: ``token`` stream events.  A node opts in with
        #: ``stream_tokens = True``; the app adds its label here at graph
        #: build time.  Empty means no token events are emitted, so
        #: structured-output nodes never flood the stream with JSON fragments.
        self.token_stream_nodes: set[str] = set()

        self.mcp_config: MCPConfig | None = None
        self.mcp_client: Client | None = None
        self.mcp_tools: list[Tool] | None = None

        self.retriever_config: RetrieverConfig | None = None
        self.stores: VSRetriever | None = None
        self.bm25_stores: BM25RetrieverManager | None = None
        # Graph-wide fallback retrieval settings.  Individual vector stores
        # may override these in the config with their own default_k / k_max /
        # k_inc values.
        self.default_k: int = 5
        self.k_max: int = 10
        self.k_inc: int = 1

        self.QueryDomainSchema: type[BaseModel] | None = None

        from klea_utils.plogging import resolve_log_level, setup_root_logger

        # The KLEA_LOG_LEVEL env var (set by --debug) overrides the
        # constructor's logging_level: when it resolves to DEBUG we show
        # full debug output regardless of the passed level.
        if resolve_log_level() == logging.DEBUG:
            stderr_level = logging.DEBUG
        else:
            stderr_level = logging_level

        setup_root_logger(
            self.graph_name,
            stderr_level=stderr_level,
            log_dir=self.paths.user_data_dir if log_file else None,
        )
        self.logger = logging.getLogger(self.graph_name)

    def _build_env_class(self) -> type[BaseModel]:
        """Build the pydantic-settings class for this graph's environment.

        The schema is derived from ``self.llm_models``: each model role ``r``
        becomes a ``{r}_model`` string field (env var ``<env_prefix>R_MODEL``),
        plus ``app_config_file``.  Generating the class from the model
        declaration keeps the env schema and the model roles as a single
        source of truth, so they cannot drift apart.

        :returns: A ``BaseSettings`` subclass configured with ``env_prefix``.
        """
        # Lazy: pydantic-settings is only needed when loading the env.
        from pydantic_settings import BaseSettings, SettingsConfigDict

        fields: dict[str, Any] = {
            f"{role}_model": (str, "") for role in self.llm_models
        }
        fields["app_config_file"] = (str, self.config_file_default)
        return create_model(
            f"{self.graph_name}Env",
            __base__=BaseSettings,
            __config__=cast(ConfigDict, SettingsConfigDict(env_prefix=self.env_prefix)),
            **fields,
        )

    def _load_env(self) -> None:
        """Load env file, and configuration

        Builds the env settings class from ``self.llm_models`` (see
        :meth:`_build_env_class`) and parses the env file (optional --
        when missing, process environment variables and class defaults are
        used), then resolves the application config file (from
        ``self.app_env.app_config_file``) via
        :func:`klea_utils.paths.resolve_app_config_path` -- the working
        directory first, then the per-app config directory.  Raises
        ``FileNotFoundError`` if the config file does not exist.
        """
        self.env_class = self._build_env_class()

        env_file_path = Path(self.env_file)
        if not env_file_path.exists():
            # Env files are optional.  With no file, pydantic-settings
            # falls back to process environment variables and class
            # defaults, so a clean machine can run with only a JSON config
            # (models are then set via shell env vars or the web UI).
            self.logger.warning(
                f"Env file not found: {self.env_file}. "
                f"Using process environment variables and defaults only. "
                f"Set {self.env_var} to specify an env file."
            )
            self.app_env = self.env_class()
        else:
            self.app_env = self.env_class(_env_file=self.env_file)
        assert self.app_env
        self.logger.debug(f"env file: {self.env_file}")
        self.logger.debug(f"env: {self.app_env}")

        # An explicit empty value means "not set" -- fall back to the field
        # default so a line like ``KLEA_AGENT_APP_CONFIG_FILE=`` does not
        # resolve to the current directory itself.
        app_config_file = self.app_env.app_config_file
        if not app_config_file:
            app_config_file = self.env_class.model_fields["app_config_file"].default
            self.logger.debug(
                f"empty app_config_file -- using default {app_config_file}"
            )

        config_file = resolve_app_config_path(
            app_config_file, get_config_dir(self.paths)
        )
        self.logger.debug(f"config file: {config_file}")
        with open(config_file, "r") as f:
            config_dict = json.load(f)
            self.logger.debug(f"{config_dict = }")
            self.app_config = self.config_class(**config_dict)
            self.logger.debug(f"{self.app_config = }")

    def _create_mcp_client(self) -> None:
        """Create MCP client from the JSON config file.

        Reads the MCP server configurations from ``self.app_env.mcp_config_file``
        and creates a ``fastmcp.Client`` instance.
        """
        if self.mcp_config and self.mcp_config.mcpServers:
            self.logger.debug(f"{self.mcp_config = }")
            self.mcp_client = Client(self.mcp_config)
        else:
            self.logger.warning("No MCP server configured.")
            self.mcp_client = None

    async def _get_mcp_tools(self) -> None:
        """Get MCP tools."""
        if self.mcp_client:
            async with self.mcp_client:
                self.mcp_tools = await self.mcp_client.list_tools()
            self.logger.debug(f"{self.mcp_tools =}")
            self._build_tools_info()

    def _build_tools_info(self) -> None:
        """Build per-domain tool metadata from fetched MCP tools."""
        self.tools_info = {}
        if not self.mcp_tools or not self.domain_mcp_configs:
            return

        # map server names to domains
        domain_servers: dict[str, list[str]] = {}
        num_servers = 0
        for domain, config in self.domain_mcp_configs.items():
            if config.mcpServers:
                domain_servers[domain] = list(config.mcpServers.keys())
                num_servers += len(list(config.mcpServers.keys()))

        # Operator capability overrides (ADR-0037), applied over annotations.
        access_overrides = self._tool_access_overrides()

        for domain, server_names in domain_servers.items():
            domain_tools_info: dict[str, ToolInfo] = {}
            for t in self.mcp_tools:
                # tools will be prefixed with server names
                if num_servers > 1 and not any(
                    t.name.startswith(s + "_") for s in server_names
                ):
                    continue
                # otherwise, there's only one server
                # Klea expects MCP tools to follow the docstring-first
                # convention (summary + Use when / Do not use for bullets +
                # one example; params via Args:), see build_tool_description
                # and docs/concepts/mcp.rst.
                full_description, short_description = build_tool_description(t)
                # Carry the standard MCP annotations so the tool access level
                # (ADR-0037) can classify the tool client-side; an absent
                # annotation stays ``None`` (fail-closed in read_only).  A
                # configured ``general.tool_access`` override takes precedence.
                annotations = getattr(t, "annotations", None)
                read_only, destructive = resolve_capability(
                    getattr(annotations, "readOnlyHint", None),
                    getattr(annotations, "destructiveHint", None),
                    access_overrides.get(t.name),
                )
                domain_tools_info[t.name] = ToolInfo(
                    title=t.title,
                    description=full_description,
                    short_description=short_description,
                    meta=clean_tool_meta(t.meta),
                    read_only=read_only,
                    destructive=destructive,
                )
            self.tools_info[domain] = domain_tools_info
        self.logger.debug(f"{self.tools_info = }")

    def _tool_access_overrides(self) -> dict[str, ToolAccessOverride]:
        """Return the configured per-tool capability overrides (ADR-0037).

        Reads ``app_config.general.tool_access``; returns an empty mapping
        when the app config does not declare it.

        :returns: ``{tool_name: ToolAccessOverride}``.
        """
        general = getattr(getattr(self, "app_config", None), "general", None)
        overrides = getattr(general, "tool_access", None)
        return dict(overrides) if overrides else {}

    def _configured_access_level(self) -> AccessLevel | None:
        """Return the config-declared access level, or ``None``.

        Reads ``app_config.general.access_level``.  Apps that do not declare
        the field (e.g. RAG, whose level is fixed) return ``None`` so the
        state default applies.

        :returns: The configured level, or ``None`` when unset.
        """
        general = getattr(getattr(self, "app_config", None), "general", None)
        raw = getattr(general, "access_level", None)
        if raw is None:
            return None
        return resolve_access_level(raw)

    def _apply_access_level_default(
        self, input_state: dict[str, Any], extra_state: dict[str, Any] | None
    ) -> None:
        """Seed the config access level unless the request overrides it.

        Precedence is request (``extra_state``) > ``general.access_level`` >
        the state field default.  Mutates *input_state* in place.

        :param input_state: The initial-state dict being built for the run.
        :param extra_state: The caller's per-run state extras, or ``None``.
        """
        if extra_state and extra_state.get("access_level") is not None:
            return
        default = self._configured_access_level()
        if default is not None:
            input_state["access_level"] = default

    def _tools_by_name(self) -> dict[str, ToolInfo]:
        """Return ``{tool_name: ToolInfo}`` flattened from ``tools_info``.

        The per-domain map is flattened for the dispatch gate, so the
        client-side path and access-level checks (ADR-0037) read one canonical
        ``ToolInfo`` per tool.  Tool names are already server-prefixed when
        several servers are configured, so a name repeated across domains is
        the same tool with the same metadata.

        :returns: Capability/metadata map keyed by tool name.
        """
        tools = {
            name: info
            for domain_tools in self.tools_info.values()
            for name, info in domain_tools.items()
        }
        self.logger.debug(f"{sorted(tools) = }")
        return tools

    async def _get_vector_stores(self) -> None:
        """Get vector stores"""
        emb = self.llm_models.get("embedding")
        if self.retriever_config and emb and emb.model_name:
            self.stores = VSRetriever(
                config=self.retriever_config,
                logger=self.logger,
                embedding_model=emb.model_name,
                default_k=self.default_k,
                k_max=self.k_max,
                k_inc=self.k_inc,
            )
            self.stores.setup()
            self.logger.info(f"Vector stores loaded: {self.stores.domains}")
        elif self.retriever_config:
            self.logger.warning(
                "No vector stores configured (missing embedding model)."
            )

        # BM25 keyword stores need no embedding model, so build them whenever
        # any domain configures bm25_stores.
        if self.retriever_config and any(
            domain.bm25_stores for domain in self.retriever_config.domains.values()
        ):
            self.bm25_stores = BM25RetrieverManager(
                config=self.retriever_config,
                logger=self.logger,
                default_k=self.default_k,
                k_max=self.k_max,
                k_inc=self.k_inc,
            )
            self.logger.info(f"BM25 stores loaded: {self.bm25_stores.domains}")

        # Build QueryDomainSchema from all configured domains (vector or BM25)
        # so BM25-only deployments still get structured classification.
        if self.retriever_config and self.retriever_config.domains:
            all_domains = list(self.retriever_config.domains.keys())
            if "undefined" not in all_domains:
                all_domains.append("undefined")
            self.QueryDomainSchema = create_model(
                "QueryDomainSchema",
                query_domains=(
                    list[Literal[tuple(all_domains)]],
                    Field(default=["undefined"], validate_default=True),
                ),
            )
        elif self.QueryDomainSchema is None:
            self.logger.warning("No domains configured for QueryDomainSchema")

    def _export_graph_png(self, filename: str) -> None:
        """Export the LangGraph as a Mermaid PNG diagram and its Mermaid source.

        Skipped when running inside Docker (``RUNNING_IN_DOCKER`` env var set).

        :param filename: Output file path for the PNG
        """
        if os.environ.get("RUNNING_IN_DOCKER"):
            return
        try:
            assert self.graph
            self.graph.get_graph().draw_mermaid_png(output_file_path=filename)
        except BaseException as e:  # noqa: BLE001
            self.logger.error("Something went wrong generating lang graph png")
            self.logger.error(e)

        # Also write the Mermaid source alongside the PNG so the explicit
        # C4 Level-3 diagrams (``devdocs/system/c4-component-*.md``) can
        # embed the auto-generated node/edge topology as their core and
        # avoid drift between code and docs (see ADR-0016/0019).
        try:
            assert self.graph
            mermaid = self.graph.get_graph().draw_mermaid()
            mermaid_path = Path(filename)
            if mermaid_path.suffix == ".png":
                mermaid_path = mermaid_path.with_suffix(".mmd")
            else:
                mermaid_path = Path(str(mermaid_path) + ".mmd")
            mermaid_path.write_text(mermaid)
            self.logger.debug(f"Wrote Mermaid source to {mermaid_path}")
        except BaseException as e:  # noqa: BLE001
            self.logger.error("Something went wrong generating lang graph mermaid")
            self.logger.error(e)

    # ------------------------------------------------------------------
    # Abstract methods -- subclasses must implement these
    # ------------------------------------------------------------------

    @abstractmethod
    def _configure_resources(self) -> None:
        """Configure vector stores and MCP servers

        Subclasses should implement this to populate ``self.retriever_config``,
        ``self.mcp_config``, and ``self.domain_mcp_configs``, which will be used
        to create the vector store class, mcp client, and per-domain tool descriptions.
        """
        ...

    @abstractmethod
    def _setup_models(self) -> None:
        """Set up LLM model instances.

        Subclasses must populate ``self.llm_models`` with ``LLMModel``
        entries keyed by role (e.g. ``"chat"``, ``"plan"``, ``"guard"``),
        setting ``required`` on each.  This runs *before* ``_load_env()``,
        so the roles declared here define the env schema generated by
        :meth:`_build_env_class`.  ``model_name`` is populated from the env
        by :meth:`_apply_model_names` after the env is loaded.  These are
        graph-wide defaults; per-request overrides arrive via the LangGraph
        Runtime context (KleaRunContext, ADR-0033).
        """
        ...

    def _apply_model_names(self) -> None:
        """Populate ``model_name`` on each LLMModel from the loaded env.

        Called by :meth:`setup` after ``_load_env()``.  Each role ``r``
        reads its default model from the generated ``app_env.<r>_model``
        field (env var ``<env_prefix>R_MODEL``).
        """
        for role, entry in self.llm_models.items():
            # The env field is generated from the role declaration, so the
            # attribute is not statically known on the settings instance.
            entry.model_name = getattr(self.app_env, f"{role}_model")

    def _apply_provider_defaults(self) -> None:
        """Populate per-provider defaults on each LLMModel from the config.

        Called by :meth:`setup` after ``_load_env()`` (which loads
        ``app_config``), since ``_setup_models`` runs before the config is
        available.
        """
        for role, entry in self.llm_models.items():
            entry.provider_defaults = self._provider_defaults_for_role(role)

    def _apply_user_agent(self) -> None:
        """Set the resolved Klea User-Agent on each ``LLMModel``.

        Gives every role the app identity sent to OpenAI-compatible endpoints
        (``klea-agent/<version>`` / ``klea-rag/<version>``).
        """
        for entry in self.llm_models.values():
            entry.user_agent = self.user_agent

    def _check_required_models(self) -> None:
        """Warn at startup when required model roles have no default model.

        Missing models are not fatal: the server still starts so the
        frontends can be used to set models per chat.  The warning lists
        all model env vars (derived as ``<env_prefix><ROLE>_MODEL``) with
        their current state so users can see exactly what to set.
        """
        missing = [
            role
            for role, entry in self.llm_models.items()
            if entry.required and not entry.model_name
        ]
        if not missing:
            return

        lines = [
            (
                f"The following models have not been set: {', '.join(missing)}. "
                "Please set them before running queries (or from the web UI, "
                "Choose models)."
            ),
            "Current configured models:",
        ]
        for role, entry in self.llm_models.items():
            env_var = f"{self.env_prefix}{role.upper()}_MODEL"
            lines.append(f"  {env_var}={entry.model_name or '<not set>'}")
        self.logger.warning("\n".join(lines))

    def _provider_defaults_for_role(self, role: str) -> dict[str, dict[str, Any]]:
        """Return per-provider default params for *role* from the config.

        Reads the optional ``providers`` config section, e.g.::

            {"huggingface": {"chat": {"max_output_tokens": 2048}}}

        and returns just the entries relevant to *role*, so each
        ``LLMModel`` carries the defaults for its own role.  Shared by all
        graphs so ``_setup_models`` implementations do not repeat it.

        :param role: Model role (``chat``, ``plan``, ``guard``, ...).
        :returns: ``{provider: {param: value}}`` for *role*.
        """
        result: dict[str, dict[str, Any]] = {}
        # ``app_config`` is the concrete ``config_class`` instance; access
        # via getattr so this works for any subclass without extra typing.
        providers = getattr(self.app_config, "providers", {})
        for provider, role_configs in providers.items():
            if role in role_configs:
                result[provider] = dict(role_configs[role])
        return result

    def _bundled_server_config(self) -> dict[str, Any] | None:
        """Build the bundled tools server config entry from the app config.

        Reads ``app_config.general.bundled_tools`` and returns ``None`` when
        the bundled tools are disabled.  The entry is a *dict*, not a
        ``StdioMCPServer`` instance, so ``MCPConfig``'s union validator picks
        ``TransformingStdioMCPServer`` whenever tag filters are present and
        applies them; without filters it stays a plain ``StdioMCPServer``.

        Apps merge the result into their ``mcpServers`` mapping; when merged
        into a domain's config the bundled tools become available to that
        domain's tool picker.

        :returns: Dict-form stdio server config, or ``None`` when disabled.
        """
        general = getattr(self.app_config, "general", None)
        bundled = getattr(general, "bundled_tools", None)
        if bundled is None or not bundled.enabled:
            self.logger.debug("Bundled tools server disabled or not configured")
            return None

        config: dict[str, Any] = {
            "command": sys.executable,
            "args": ["-m", "klea_utils.mcp.server.bundled"],
        }
        if bundled.include_tags:
            config["include_tags"] = list(bundled.include_tags)
        if bundled.exclude_tags:
            config["exclude_tags"] = list(bundled.exclude_tags)
        self.logger.debug(f"Bundled tools server config: {config}")
        return config

    @abstractmethod
    async def _create_graph(self) -> None:
        """Build and compile the LangGraph, storing it in ``self.graph``.

        This is where subclasses define their nodes, edges, and conditional
        routing logic.
        """
        ...

    # ------------------------------------------------------------------
    # Hook methods -- override for pre/post setup work
    # ------------------------------------------------------------------

    def get_allowed_msgpack_modules(self) -> list[type | tuple[str, ...]]:
        """Return types allowed for checkpoint msgpack deserialization.

        Subclasses should override to add their state schemas
        (e.g. ``EvaluateAnswerSchema``, ``RetrievalQueryOutput``).  The base
        list covers shared utils types checkpointed by all graphs.
        """
        from fastmcp.client.client import CallToolResult as FastMCPCallToolResult
        from mcp.types import EmbeddedResource, ImageContent, TextContent

        from klea_utils.graph.schemas import TokenUsage

        modules: list[type | tuple[str, ...]] = [
            TokenUsage,
            ToolCallSchema,
            FastMCPCallToolResult,
            TextContent,
            ImageContent,
            EmbeddedResource,
        ]
        try:
            from mcp.types import AudioContent

            modules.append(AudioContent)
        except ImportError:
            pass
        try:
            from mcp.types import CallToolResult as McpCallToolResult

            modules.append(McpCallToolResult)
        except ImportError:
            pass
        return modules

    def context_snapshot(self, state: dict[str, Any]) -> dict[str, Any] | None:
        """Return the session context to surface as a graph-level event.

        Called by :meth:`run_graph_astream_events` on every per-superstep
        ``values`` state snapshot.  Returning a dict publishes a
        ``context`` stream event (change-deduped); returning ``None``
        (the default) publishes nothing.

        Session context is a *projection of state*, not a node-authored
        message: the app defines what the snapshot is by overriding this
        hook (e.g. the agent's operating mode), while the emission itself
        is structural -- nodes cannot write a ``context`` event through
        the ``custom`` channel.

        :param state: The current graph state snapshot (a dict).
        :returns: A JSON-serializable dict for the ``context`` event, or
            ``None`` if there is no session context to surface.
        """
        return None

    def _pre_setup(self) -> None:
        """Hook called before the standard setup sequence.

        Override to perform subclass-specific initialisation before
        config loading and model setup.
        """
        pass

    async def _setup_checkpointer(self) -> None:
        """Set up the checkpointer.

        ``setup()`` calls this hook before ``_load_env()``.
        The checkpointer is ``None`` when ``checkpoint="none"``.
        """
        if self.checkpointer_mode == "sqlite":
            import aiosqlite
            from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer

            db_path = init_dir(self.paths.user_data_dir) / "checkpoints.db"
            self.logger.debug("Opening sqlite checkpointer at %s", db_path)
            conn = await aiosqlite.connect(str(db_path))
            serde = JsonPlusSerializer(
                allowed_msgpack_modules=self.get_allowed_msgpack_modules()
            )
            self.checkpointer = AsyncSqliteSaver(conn, serde=serde)
            # Keep raw connection for lifespan cleanup (AsyncSqliteSaver holds it as .conn)
            self._checkpointer_conn = conn  # type: ignore[attr-defined]
            self.logger.debug("Sqlite checkpointer ready")
        elif self.checkpointer_mode == "inmemory":
            from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer

            serde = JsonPlusSerializer(
                allowed_msgpack_modules=self.get_allowed_msgpack_modules()
            )
            self.checkpointer = InMemorySaver(serde=serde)
            self.logger.debug("In-memory checkpointer ready")

    def _post_setup(self) -> None:
        """Hook called after the standard setup sequence.

        Override to perform subclass-specific finalisation after
        the LangGraph has been compiled.
        """
        pass

    # ------------------------------------------------------------------
    # Template method
    # ------------------------------------------------------------------

    async def _pre_graph(self) -> None:
        """Hook called after MCP client setup but before graph compilation.

        Override to perform subclass-specific initialisation that depends
        on config and MCP client but must happen         before the LangGraph is built.
        """
        pass

    @final
    async def setup(self) -> None:
        """Set up the orchestrator.

        Calls hooks and template methods in this order:

        #. ``_pre_setup()``
        #. ``_setup_checkpointer()``
        #. ``_setup_models()``: build ``self.llm_models`` (roles and required
           flags) that the env schema is generated from.
        #. ``_load_env()``: parse the env into ``self.app_env`` using the
           schema generated from ``llm_models``, then load the JSON config.
        #. ``_configure_resources()``
        #. ``_check_required_models()``
        #. ``_create_mcp_client()``
        #. ``_pre_graph()``
        #. ``_create_graph()``
        #. ``_post_setup()``
        """
        self._pre_setup()
        warn_if_root(self.logger)
        await self._setup_checkpointer()
        self._setup_models()
        self._load_env()
        self._apply_model_names()
        self._apply_provider_defaults()
        self._apply_user_agent()
        # ``llm_models`` is now fully populated (roles, model names, required
        # flags, provider defaults) -- log the resolved config once.
        self.logger.debug(
            "Resolved model configuration:\n"
            + "\n".join(
                f"  {role}: "
                f"model={entry.model_name or '<not set>'}, "
                f"required={entry.required}, "
                f"modifiable={entry.modifiable}"
                for role, entry in self.llm_models.items()
            )
        )
        self._configure_resources()
        self._check_required_models()
        self._create_mcp_client()
        await self._get_mcp_tools()
        await self._get_vector_stores()
        await self._pre_graph()
        await self._create_graph()
        self._post_setup()

    # ------------------------------------------------------------------
    # Execution methods -- identical across all implementations
    # ------------------------------------------------------------------

    async def run_graph_invoke_state(
        self, state: dict, thread_id: str = "default_thread"
    ) -> dict:
        """Run the graph, accepting and returning full state dicts.

        :param state: Initial graph state (must contain ``query`` key)
        :param thread_id: Session/thread identifier for checkpointing
        :returns: Final graph state
        """
        config: RunnableConfig = {"configurable": {"thread_id": thread_id}}

        if "query" not in state:
            raise ValueError("state must contain 'query' key")
        if self.graph is None:
            raise RuntimeError("Graph not compiled. Call setup() first.")

        final_state = await self.graph.ainvoke(state, config=config)
        self.logger.debug(final_state)
        return final_state

    async def run_graph_invoke(
        self,
        query: str,
        thread_id: str = "default_thread",
        *,
        extra_state: dict[str, Any] | None = None,
        context: KleaRunContext | dict[str, Any] | None = None,
    ) -> str:
        """Run the graph with a simple string query.

        :param query: User query string
        :param thread_id: Session/thread identifier for checkpointing
        :param extra_state: Optional initial state fields merged into the
            invocation (e.g. an app-specific ``mode`` request).  These are
            passed to ``graph.ainvoke`` alongside ``query`` and validated
            against the graph's state schema.
        :param context: Per-run runtime context (ADR-0033), forwarded
            verbatim to the graph run.  Accepts a ``KleaRunContext`` (or
            app subclass) instance or a plain dict -- dicts are
            coerced/validated against the graph's ``context_schema`` at
            the run boundary.  The base contract is generic:
            ``model_overrides`` is a framework-provided convention (apps
            may diverge at the node layer) and apps add their own keys.
        :returns: The ``message_for_user`` field from the final state

        :note: This is a bare ``ainvoke`` with no ``values``-event loop, so
            ``context_snapshot`` is never invoked here and the session
            context projection is not produced on this path.  Callers that
            need it should use :meth:`run_graph_astream_events` (``context``
            stream events) or the hydration endpoint
            ``GET /chat/{user_id}/{chat_id}/context`` (ADR-0032).
        """
        config: RunnableConfig = {"configurable": {"thread_id": thread_id}}

        if self.graph is None:
            raise RuntimeError("Graph not compiled. Call setup() first.")

        input_state = {"query": query}
        if extra_state:
            input_state.update(extra_state)
        self._apply_access_level_default(input_state, extra_state)
        final_state = await self.graph.ainvoke(
            input_state, config=config, context=context
        )

        self.logger.debug(f"{final_state =}")
        if message := final_state.get("message_for_user", None):
            return message
        else:
            return "I was unable to answer"

    async def run_graph_stream(
        self,
        query: str,
        thread_id: str = "default_thread",
        *,
        extra_state: dict[str, Any] | None = None,
        context: KleaRunContext | dict[str, Any] | None = None,
    ):
        """Run the graph and yield intermediate ``message_for_user`` values.

        :param query: User query string
        :param thread_id: Session/thread identifier for checkpointing
        :param extra_state: Optional initial state fields merged into the
            invocation alongside ``query`` (e.g. an app-specific ``mode``
            request).
        :param context: Per-run runtime context (ADR-0033), forwarded
            verbatim to the graph run (see :meth:`run_graph_invoke`).
        :yields: ``message_for_user`` strings from each node
        """
        config: RunnableConfig = {"configurable": {"thread_id": thread_id}}

        if self.graph is None:
            raise RuntimeError("Graph not compiled. Call setup() first.")

        input_state = {"query": query}
        if extra_state:
            input_state.update(extra_state)
        self._apply_access_level_default(input_state, extra_state)
        async for chunk in self.graph.astream(
            input_state, config=config, context=context
        ):
            for node, state in chunk.items():
                self.logger.debug(f"{node}: {state!r}")
                if message := state.get("message_for_user", None):
                    self.logger.info(f"User message: {message}")
                    yield message
                else:
                    self.logger.debug(f"Working in node: {node}")

    async def graph_stream(
        self,
        query: str,
        thread_id: str = "default_thread",
        *,
        extra_state: dict[str, Any] | None = None,
        context: KleaRunContext | dict[str, Any] | None = None,
    ) -> Any:
        """Run the graph and return the raw astream result.

        :param query: User query string
        :param thread_id: Session/thread identifier for checkpointing
        :param extra_state: Optional initial state fields merged into the
            invocation alongside ``query`` (e.g. an app-specific ``mode``
            request).
        :param context: Per-run runtime context (ADR-0033), forwarded
            verbatim to the graph run (see :meth:`run_graph_invoke`).
        :returns: Raw async generator from ``graph.astream()``
        """
        config: RunnableConfig = {"configurable": {"thread_id": thread_id}}

        if self.graph is None:
            raise RuntimeError("Graph not compiled. Call setup() first.")

        input_state = {"query": query}
        if extra_state:
            input_state.update(extra_state)
        self._apply_access_level_default(input_state, extra_state)
        res = self.graph.astream(input_state, config=config, context=context)
        return res

    async def run_graph_astream_events(
        self,
        query: str,
        thread_id: str = "default_thread",
        *,
        extra_state: dict[str, Any] | None = None,
        context: KleaRunContext | dict[str, Any] | None = None,
    ):
        """Run the graph and yield structured streaming events.

        Yields dicts with:

        ``{"type": "progress", "node": "<label>"}``
            When the graph enters a new node (via ``write_custom_stream``)
        ``{"type": "inspect", "node": "<label>", "data": {...}}``
            Inspection data from a node after execution (summary + details)
        ``{"type": "token", "content": "<chunk>", "node": "<label>"}``
            LLM token chunk from the current node.  Emitted only for nodes in
            :attr:`token_stream_nodes` (free-text answer nodes), not for
            structured-output nodes.
        ``{"type": "usage", "node": "<label>", "data": {...}}``
            Per-node token usage (input / output / total tokens)
        ``{"type": "context", "data": {...}}``
            Session-level context (app-defined, e.g. an operating mode and
            its assurance), emitted by a node via a ``context`` custom event
        ``{"type": "complete", "message_for_user": "<answer>"}``
            Final answer from the completed graph

        Uses LangGraph's ``astream_events`` v3 protocol.  Progress events
        from all nodes (LLM and non-LLM) arrive via the ``custom`` channel.
        LLM token output is read from the ``messages`` channel.
        A ``StreamTransformer`` enables the custom channel so those events
        flow through.

        Reference: https://docs.langchain.com/oss/python/langgraph/event-streaming

        :param query: User query string
        :param thread_id: Session/thread identifier for checkpointing
        :param extra_state: Optional initial state fields merged into the
            invocation alongside ``query`` (e.g. an app-specific ``mode``
            request).
        :param context: Per-run runtime context (ADR-0033), forwarded
            verbatim to the graph run (see :meth:`run_graph_invoke`).
        :yields: Structured event dicts
        """
        config: RunnableConfig = {"configurable": {"thread_id": thread_id}}

        if self.graph is None:
            raise RuntimeError("Graph not compiled. Call setup() first.")

        input_state = {"query": query}
        if extra_state:
            input_state.update(extra_state)
        self._apply_access_level_default(input_state, extra_state)
        stream = await self.graph.astream_events(
            input_state,
            config=config,
            version="v3",
            transformers=[_CustomChannelEnabler],
            context=context,
        )

        current_node = ""
        node_start = time.monotonic()
        total_start = time.monotonic()
        last_values: dict = {}
        # Last emitted session-context snapshot, for change-dedup.
        last_context: dict[str, Any] | None = None

        async for event in stream:
            method = event["method"]

            if method == "custom":
                data = event["params"]["data"]
                if not isinstance(data, dict):
                    continue

                event_type = data.get("type")

                if event_type == "progress":
                    node = data.get("node")
                    if not node:
                        continue
                    if node != current_node:
                        now = time.monotonic()
                        if current_node:
                            self.logger.debug(
                                "Node [%s] took %.2fs",
                                current_node,
                                now - node_start,
                            )
                        node_start = now
                        current_node = node
                        self.logger.debug(f"Progress: {current_node}")
                        yield {"type": "progress", "node": current_node}

                elif event_type in ("inspect", "state", "usage", "tool"):
                    node = data.get("node")
                    if not node:
                        continue
                    data_out = data.get("data", {}).copy()
                    data_out["timing_seconds"] = round(time.monotonic() - node_start, 2)
                    yield {
                        "type": event_type,
                        "node": node,
                        "data": data_out,
                    }

            elif method == "messages":
                # Token deltas are only useful for user-facing free-text
                # nodes; structured-output nodes emit JSON fragments and are
                # not in ``token_stream_nodes`` (see its docstring).
                if current_node not in self.token_stream_nodes:
                    continue
                data = event["params"]["data"]
                for item in data:
                    if not isinstance(item, dict):
                        continue

                    if item.get("event") == "content-block-delta":
                        delta = item.get("delta", {})
                        if "text" in delta:
                            yield {
                                "type": "token",
                                "content": delta["text"],
                                "node": current_node,
                            }

            elif method == "values":
                last_values = event["params"]["data"]
                # ``values`` snapshots arrive as a dict (TypedDict state) or
                # as the pydantic state instance; ``_normalise_state_snapshot``
                # gives ``context_snapshot`` a plain dict in the stream path --
                # the same normalization the hydration endpoint applies on a
                # direct checkpoint read (see ``klea_utils.api.context``).
                snapshot = _normalise_state_snapshot(last_values)
                # Graph-level session context is a *projection of state*: it is
                # derived here from the per-superstep ``values`` snapshot via
                # ``context_snapshot`` (default None), never written by a node.
                # This keeps the event structural -- nodes cannot author a
                # ``context`` event -- while the app defines what context means.
                # (Local named context_data: the method's ``context`` parameter
                # is the ADR-0033 per-run runtime context.)
                context_data = self.context_snapshot(snapshot)
                if context_data is not None and context_data != last_context:
                    last_context = context_data
                    yield {"type": "context", "data": context_data}

        total_elapsed = time.monotonic() - total_start
        if current_node:
            self.logger.debug(
                "Node [%s] took %.2fs",
                current_node,
                time.monotonic() - node_start,
            )
        self.logger.info("Graph completed in %.2fs", total_elapsed)

        message = ""
        if last_values:
            if isinstance(last_values, dict):
                message = last_values.get("message_for_user", "")
            elif hasattr(last_values, "message_for_user"):
                message = last_values.message_for_user
        yield {"type": "complete", "message_for_user": message}
