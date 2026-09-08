#!/usr/bin/env python3
"""
General RAG implementation

File: rag.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail DOT com>
"""

import logging
from pathlib import Path
from typing import final, override

from fastmcp.mcp_config import MCPConfig
from klea_utils.graph.base import BaseLangGraph
from klea_utils.graph.context import KleaRunContext
from klea_utils.llm import create_configurable_model
from klea_utils.nodes.answer_general import AnswerGeneral, FallbackConfig
from klea_utils.nodes.fixed_answer import FixedAnswer
from klea_utils.nodes.guard import GuardNode
from klea_utils.nodes.guard_router import GuardRouterNode
from klea_utils.nodes.summarise_memory import SummariseMemoryNode
from klea_utils.nodes.tools_caller import ToolsCallerNode
from klea_utils.nodes.tools_picker import ToolsPicker
from klea_utils.stores.config import FilterFieldInfo, RetrieverConfig
from klea_utils.stores.retrieval.base import BaseKleaRetriever
from langgraph.graph import END, START, StateGraph

from .config import AppConfig
from .nodes.answer_from_context import AnswerFromContext
from .nodes.answer_user import AnswerUser
from .nodes.classify_question import ClassifyQuestion
from .nodes.evaluator import Evaluator
from .nodes.generate_retrieval_query import GenerateRetrievalQuery
from .nodes.init_rag import InitRAGState
from .nodes.retrieve_info import RetrieveInfoNode
from .nodes.route_evaluator import RouteEvaluator
from .nodes.route_query import RouteQuery
from .schemas import EvaluateAnswerSchema, RAGState, RetrievalQueryOutput


@final
class RAG(BaseLangGraph):
    """General RAG implementation"""

    env_prefix = "KLEA_RAG_"
    env_var = "KLEA_RAG_ENV_FILE"
    env_file_default = "rag.env"
    config_class = AppConfig
    config_file_default = "klea_rag.json"
    graph_name = "klea-rag"

    # type hints
    app_config: AppConfig

    def __init__(
        self,
        logging_level: int = logging.INFO,
        checkpoint: str = "inmemory",
    ):
        """Initialise"""
        super().__init__(logging_level=logging_level, checkpoint=checkpoint)

    def get_allowed_msgpack_modules(self) -> list[type | tuple[str, ...]]:
        """Extend base allowlist with RAG-specific checkpointed schemas."""
        base = super().get_allowed_msgpack_modules()
        return base + [EvaluateAnswerSchema, RetrievalQueryOutput]

    def _has_vector_stores(self) -> bool:
        """Return whether any configured domain declares vector stores.

        Vector stores load at startup from the embedding model, so an
        embedding override set later per chat cannot enable retrieval.
        BM25-only domains need no embedding model.
        """
        return bool(
            self.retriever_config
            and any(
                domain.vector_stores
                for domain in self.retriever_config.domains.values()
            )
        )

    @override
    def _setup_models(self) -> None:
        """Set up the LLM chat model

        A single ``_ConfigurableModel`` is shared across the chat roles.
        Each role's ``model_name`` is populated by the base class from the
        ``{role}_model`` env field after the env is loaded.  The embedding
        role has no chat instance; it only carries the embedding model name
        used to load vector stores at startup.  The guard role is optional
        and not modifiable per request.
        """
        from klea_utils.llm import LLMModel

        model = create_configurable_model(logger=self.logger)
        self.llm_models = {
            "chat": LLMModel(
                instance=model,
                required=True,
            ),
            "guard": LLMModel(
                instance=model,
                required=False,
                modifiable=False,
            ),
            # required is adjusted in ``_configure_resources`` once the
            # vector store configuration is known.
            "embedding": LLMModel(
                instance=None,
                required=True,
            ),
        }

    async def get_graph(self):
        """Setup and get compiled graph"""
        await self.setup()
        return self.graph

    @override
    async def _pre_graph(self):
        "Set up bits required before graph is compiled"
        # for refusal node
        self.refusal_message = "Sorry. I cannot answer this query as it does not fall into my permitted domains. Available domains are:\n"
        self.refusal_message += "\n- ".join([""] + list(self.app_config.domains))
        self.refusal_message += "\n\n\nPlease try another query."

        # for clarification node
        self.clarification_message = "Apologies. I could not answer that question. Can you please ask another one or try to reword it and I will try again?"

    def _splitter_node(self, state: RAGState):
        return {}

    @override
    def _configure_resources(self):
        """Configure resources"""
        assert self.app_config
        domains = self.app_config.domains
        domain_stores = {}
        domain_ms = {}
        for d, inf in domains.items():
            domain_stores[d] = inf.model_dump(
                include={
                    "vector_stores",
                    "bm25_stores",
                    "description",
                    "filter_fields",
                }
            )

            # flat config for mcp client initialization
            domain_ms.update(inf.model_dump(include={"mcp_servers"})["mcp_servers"])

        self.logger.debug(f"{domain_stores = }")
        self.logger.debug(f"{domain_ms = }")

        # set up configs
        self.retriever_config = RetrieverConfig(domains=domain_stores)
        self.default_k = self.app_config.general.default_k
        self.k_max = self.app_config.general.k_max
        self.k_inc = self.app_config.general.k_inc
        self.max_refs_size = self.app_config.general.max_refs_size

        # The bundled tools server, when enabled, is a general, domain-agnostic
        # tool source: it is made available to every configured domain.
        bundled = self._bundled_server_config()
        if bundled:
            domain_ms["bundled"] = bundled
            self.logger.debug("Bundled tools server enabled across all domains")
        self.mcp_config = MCPConfig(mcpServers=domain_ms)

        # The embedding model is only required when vector stores are
        # configured.  ``_check_required_models`` runs after this, so adjust
        # the flag now that the store configuration is known.
        if "embedding" in self.llm_models:
            self.llm_models["embedding"].required = self._has_vector_stores()

        # store per-domain MCP configs for domain-aware tool descriptions
        self.domain_mcp_configs = {}
        for d, inf in domains.items():
            domain_servers = inf.model_dump(include={"mcp_servers"}).get(
                "mcp_servers", {}
            )
            if bundled:
                domain_servers = {**domain_servers, "bundled": bundled}
            if domain_servers:
                self.domain_mcp_configs[d] = MCPConfig(mcpServers=domain_servers)

    @override
    async def _create_graph(self):
        """Create the LangGraph"""
        self.workflow = StateGraph(RAGState, context_schema=KleaRunContext)

        # Guard nodes
        self._guard_node = GuardNode(
            logger=self.logger,
            label="Checking safety",
            llm_models=self.llm_models,
            memory=self.memory,
        )
        self.workflow.add_node(self._guard_node.label, self._guard_node.execute)

        self._guard_router_node = GuardRouterNode(
            logger=self.logger, label="Routing safety"
        )

        self._decline_to_answer_node = FixedAnswer(
            logger=self.logger,
            label="Declining query",
            state_attr="message_for_user",
            message="I cannot respond to this query. Please try another.",
        )
        self.workflow.add_node(
            self._decline_to_answer_node.label, self._decline_to_answer_node.execute
        )

        self._init_rag_state_node = InitRAGState(
            logger=self.logger, label="Initializing"
        )
        self.workflow.add_node(
            self._init_rag_state_node.label, self._init_rag_state_node.execute
        )
        self._classify_question_node = ClassifyQuestion(
            logger=self.logger,
            label="Classifying question",
            llm_models=self.llm_models,
            output_schema=self.QueryDomainSchema,
            memory=self.memory,
            domains={
                d: info.description for d, info in self.app_config.domains.items()
            },
            pre_prompt=self.app_config.general.pre_prompt,
        )
        self.workflow.add_node(
            self._classify_question_node.label, self._classify_question_node.execute
        )

        self._route_query_domain_node = RouteQuery(
            logger=self.logger,
            label="Routing question",
            non_domain_chat=self.app_config.general.non_domain_chat,
        )

        # Domains are configured (with their filter fields) in
        # ``_configure_resources`` before the graph is built.
        assert self.retriever_config
        filter_fields_by_domain: dict[str, list[FilterFieldInfo]] = {
            d: inf.filter_fields for d, inf in self.retriever_config.domains.items()
        }
        self._generate_retrieval_query_node = GenerateRetrievalQuery(
            logger=self.logger,
            label="Generating search",
            llm_models=self.llm_models,
            filter_fields_by_domain=filter_fields_by_domain,
        )
        self.workflow.add_node(
            self._generate_retrieval_query_node.label,
            self._generate_retrieval_query_node.execute,
        )
        self._tools_picker_node = ToolsPicker(
            logger=self.logger,
            label="Selecting tools",
            llm_models=self.llm_models,
            tools_info=self.tools_info,
            model_type="chat",
            prompt_registry_location=Path(__file__).parent / "nodes" / "prompts",
        )
        self.workflow.add_node(
            self._tools_picker_node.label, self._tools_picker_node.execute
        )

        self._tools_caller_node = ToolsCallerNode(
            logger=self.logger,
            label="Running tools",
            mcp_client=self.mcp_client,
            tools_meta={t.name: t.meta for t in (self.mcp_tools or []) if t.meta},
        )
        self.workflow.add_node(
            self._tools_caller_node.label, self._tools_caller_node.execute
        )

        self._answer_general_node = AnswerGeneral(
            logger=self.logger,
            label="Answering generally",
            llm_models=self.llm_models,
            memory=self.memory,
            fallback_config=FallbackConfig(
                enabled=self.app_config.general.fallback_to_training_data,
                warning=self.app_config.general.fallback_warning,
            ),
        )
        self.workflow.add_node(
            self._answer_general_node.label, self._answer_general_node.execute
        )

        self._refuse_answer_node = FixedAnswer(
            logger=self.logger,
            label="Refusing query",
            state_attr="message_for_user",
            message=self.refusal_message,
        )
        self.workflow.add_node(
            self._refuse_answer_node.label, self._refuse_answer_node.execute
        )

        # All configured retrievers (vector stores and/or BM25 stores)
        retrievers: list[BaseKleaRetriever] = [
            r for r in (self.stores, self.bm25_stores) if r is not None
        ]
        if not retrievers:
            self.logger.warning(
                "No retrievers (vector or BM25) configured for any domain"
            )

        self._retrieve_info_node = RetrieveInfoNode(
            logger=self.logger,
            label="Retrieving information",
            retrievers=retrievers,
            max_refs_size=self.max_refs_size,
            filter_fields_by_domain=filter_fields_by_domain,
        )
        self.workflow.add_node(
            self._retrieve_info_node.label, self._retrieve_info_node.execute
        )
        self._generate_answer_from_context_node = AnswerFromContext(
            logger=self.logger,
            label="Generating answer",
            llm_models=self.llm_models,
            memory=False,
        )
        self.workflow.add_node(
            self._generate_answer_from_context_node.label,
            self._generate_answer_from_context_node.execute,
        )
        self._evaluate_answer_node = Evaluator(
            logger=self.logger,
            label="Evaluating answer",
            llm_models=self.llm_models,
        )
        self.workflow.add_node(
            self._evaluate_answer_node.label, self._evaluate_answer_node.execute
        )

        self._route_evaluator_node = RouteEvaluator(
            logger=self.logger,
            label="Routing evaluation",
            retrievers=retrievers,
            max_retrieval_attempts=self.app_config.general.max_retrieval_attempts,
            max_rewrite_attempts=self.app_config.general.max_rewrite_attempts,
            fallback_to_training_data=self.app_config.general.fallback_to_training_data,
        )

        self._answer_user_node = AnswerUser(
            logger=self.logger, label="Preparing response"
        )
        self.workflow.add_node(
            self._answer_user_node.label, self._answer_user_node.execute
        )

        self._ask_user_for_clarification_node = FixedAnswer(
            logger=self.logger,
            label="Requesting clarification",
            state_attr="message_for_user",
            message=self.clarification_message,
        )
        self.workflow.add_node(
            self._ask_user_for_clarification_node.label,
            self._ask_user_for_clarification_node.execute,
        )

        if self.memory:
            self._summarise_history_node = SummariseMemoryNode(
                logger=self.logger,
                label="Summarizing history",
                llm_models=self.llm_models,
                summarisation_threshold_chars=10_000,
                num_history_chars=10_000,
            )
            self.workflow.add_node(
                self._summarise_history_node.label,
                self._summarise_history_node.execute,
            )

        self._splitter_label = "Splitting"

        self.workflow.add_edge(START, self._init_rag_state_node.label)
        self.workflow.add_edge(self._init_rag_state_node.label, self._guard_node.label)
        self.workflow.add_conditional_edges(
            self._guard_node.label,
            self._guard_router_node.execute,
            {
                "safe": self._classify_question_node.label,
                "unsafe": self._decline_to_answer_node.label,
            },
        )

        self.workflow.add_node(self._splitter_label, self._splitter_node)

        self.workflow.add_conditional_edges(
            self._classify_question_node.label,
            self._route_query_domain_node.execute,
            {
                "domain_query": self._splitter_label,
                "non_domain_query": self._answer_general_node.label,
                "non_domain_refuse": self._refuse_answer_node.label,
            },
        )
        self.workflow.add_edge(
            self._splitter_label, self._generate_retrieval_query_node.label
        )
        self.workflow.add_edge(self._splitter_label, self._tools_picker_node.label)
        self.workflow.add_edge(
            self._tools_picker_node.label, self._tools_caller_node.label
        )
        self.workflow.add_edge(
            self._generate_retrieval_query_node.label,
            self._retrieve_info_node.label,
        )
        self.workflow.add_edge(
            self._retrieve_info_node.label,
            self._generate_answer_from_context_node.label,
        )
        self.workflow.add_edge(
            self._tools_caller_node.label,
            self._generate_answer_from_context_node.label,
        )
        self.workflow.add_edge(
            self._generate_answer_from_context_node.label,
            self._evaluate_answer_node.label,
        )

        self.workflow.add_conditional_edges(
            self._evaluate_answer_node.label,
            self._route_evaluator_node.execute,
            {
                "continue": self._answer_user_node.label,
                "retrieve_more_info": self._retrieve_info_node.label,
                "rewrite_answer": self._generate_answer_from_context_node.label,
                "modify_query": self._generate_retrieval_query_node.label,
                "fallback": self._answer_general_node.label,
                "best_effort": self._answer_user_node.label,
                "undefined": self._ask_user_for_clarification_node.label,
            },
        )

        if self.memory:
            self.workflow.add_edge(
                self._answer_user_node.label,
                self._summarise_history_node.label,
            )
            self.workflow.add_edge(
                self._ask_user_for_clarification_node.label,
                self._summarise_history_node.label,
            )
            self.workflow.add_edge(
                self._answer_general_node.label,
                self._summarise_history_node.label,
            )
            self.workflow.add_edge(self._summarise_history_node.label, END)
        else:
            self.workflow.add_edge(self._answer_user_node.label, END)
            self.workflow.add_edge(self._ask_user_for_clarification_node.label, END)
            self.workflow.add_edge(self._answer_general_node.label, END)

        self.workflow.add_edge(self._decline_to_answer_node.label, END)
        self.workflow.add_edge(self._refuse_answer_node.label, END)

        if self.checkpointer:
            self.graph = self.workflow.compile(checkpointer=self.checkpointer)
        else:
            self.graph = self.workflow.compile()

        self._export_graph_png("rag-lang-graph.png")
