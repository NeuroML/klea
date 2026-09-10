#!/usr/bin/env python3
"""
Klea agent framework implementation

File: klea_agent.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail DOT com>
"""

import logging
from pathlib import Path
from typing import Any, final, override

from fastmcp.client.client import CallToolResult
from fastmcp.mcp_config import MCPConfig
from klea_utils.graph.base import BaseLangGraph
from klea_utils.graph.context import KleaRunContext
from klea_utils.llm import create_configurable_model
from klea_utils.nodes.fixed_answer import FixedAnswer
from klea_utils.nodes.guard import GuardNode
from klea_utils.nodes.guard_router import GuardRouterNode
from klea_utils.nodes.summarise_memory import SummariseMemoryNode
from klea_utils.nodes.tools_caller import ToolsCallerNode
from klea_utils.nodes.tools_picker import ToolsPicker
from langgraph.graph import END, START, StateGraph

from klea_agent.nodes.answer_user import AnswerUser
from klea_agent.nodes.evaluator import Evaluator
from klea_agent.nodes.explore_planner import ExplorePlanner
from klea_agent.nodes.goal_setter import GoalSetter
from klea_agent.nodes.init_graph import InitGraphState
from klea_agent.nodes.mode_router import ModeDecision, ModeInformer
from klea_agent.nodes.planner import Planner
from klea_agent.nodes.tools_router import ToolsRouter

from .config import AppConfig
from .schemas import (
    ArtefactSchema,
    CodeSchema,
    Discovery,
    EvaluationSchema,
    GoalSchema,
    KleaAgentState,
    Mode,
    PlanSchema,
    RouteSchema,
    StepSchema,
)


@final
class KleaAgent(BaseLangGraph):
    """Klea Agent implementation"""

    env_prefix = "KLEA_AGENT_"
    env_var = "KLEA_AGENT_ENV_FILE"
    env_file_default = "klea_agent.env"
    config_class = AppConfig
    config_file_default = "klea_agent.json"
    graph_name = "klea"

    # type hints
    app_config: AppConfig

    def __init__(
        self,
        logging_level: int = logging.INFO,
        checkpoint: str = "inmemory",
    ):
        """Initialise"""
        super().__init__(logging_level=logging_level, checkpoint=checkpoint)

    @override
    def _setup_models(self) -> None:
        """Set up the LLM chat model

        A single ``_ConfigurableModel`` is shared across all roles.  Each
        role's ``model_name`` is populated by the base class from the
        ``{role}_model`` env field after the env is loaded.  The guard role
        is optional and not modifiable per request.
        """
        from klea_utils.llm import LLMModel

        model = create_configurable_model(logger=self.logger)

        self.llm_models = {
            "chat": LLMModel(
                instance=model,
                required=True,
            ),
            "plan": LLMModel(
                instance=model,
                required=True,
            ),
            "guard": LLMModel(
                instance=model,
                required=False,
                modifiable=False,
            ),
        }

    @override
    def get_allowed_msgpack_modules(self) -> list[type | tuple[str, ...]]:
        """Extend base allowlist with Agent-specific checkpointed schemas.

        Mirrors ``rag_pkg/klea_rag/rag.py:get_allowed_msgpack_modules``  ---  the
        base list (``TokenUsage``, ``ToolCallSchema``, ``CallToolResult``, ...)
        is extended with schemas that are stored in the checkpoint.  The base
        already probes for ``AudioContent``/``McpCallToolResult``.
        """
        base = super().get_allowed_msgpack_modules()
        return base + [
            CodeSchema,
            StepSchema,
            PlanSchema,
            GoalSchema,
            ArtefactSchema,
            Discovery,
            Mode,
            RouteSchema,
            EvaluationSchema,
        ]

    @override
    def _configure_resources(self) -> None:
        """Configure MCP servers and a default domain.

        Merges the external MCP server (if any) with the bundled tools server
        into a single ``MCPConfig``, and sets up a single domain that includes
        both so tool descriptions are built correctly.  This mirrors the
        per-domain merging in ``rag_pkg/klea_rag/rag.py:_configure_resources``
        but with the agent's single ``code`` domain.  Retrieval
        (``RetrieverConfig``/``default_k``/``k_max``) remains deferred
        until the ADR-0029 retrieval phase.
        """
        all_servers: dict[str, Any] = dict(self.app_config.mcp_servers)
        if bundled := self._bundled_server_config():
            all_servers["bundled"] = bundled
        else:
            self.logger.info("Bundled tools server disabled")

        self.mcp_config = MCPConfig(mcpServers=all_servers)
        self.domain_mcp_configs = {"code": MCPConfig(mcpServers=all_servers)}

    @override
    async def _pre_graph(self) -> None:
        """Hook before graph compilation  ---  parity with RAG.

        Currently a no-op; reserved for wiring that depends on the MCP client
        but must happen before ``_create_graph`` (e.g. future retrieval setup).
        """

    async def get_graph(self):
        """Setup and return the compiled graph (helper for tests/docs)."""
        await self.setup()
        return self.graph

    # TODO: replace with dedicated router node (see ``RouteEvaluator`` in RAG)
    async def _step_router_node(self, state: KleaAgentState) -> str:
        """Return ``plan.status`` for conditional routing."""
        return state.plan.status

    async def _mode_router_node(self, state: KleaAgentState) -> str:
        """Route mode decision: proceed normally or inform (ADR-0030).

        ``mode.note`` is only set when a requested mode cannot run (e.g.
        Scientific mode without a curated knowledge source), so returning
        ``"inform"`` routes to the terminal ModeInformer node instead of
        silently downgrading to an unverified answer.
        """
        return "inform" if state.mode.note else "proceed"

    @override
    def context_snapshot(self, state: dict[str, Any]) -> dict[str, Any] | None:
        """Surface the operating mode as a ``context`` event.

        The session context is a projection of the checkpointed state
        written by :class:`ModeDecision` at task entry (ADR-0032): the
        graph streamer publishes it (change-deduped) on the ``values``
        channel, so the frontend can render the active mode and any
        explanation note.  Verification/assurance tracking is deferred to
        the ADR-0029 verification phase.

        ``requested`` is included because ``Mode`` is a whole-object
        field (no reducer): after a page reload the web UI's
        ``query_extra`` is empty, so the next query would send
        ``requested=general`` and silently overwrite/ reset the
        checkpointed mode back to general.  Hydration restores
        ``requested`` into the selector, keeping the re-request aligned
        with the user's last intent.

        :param state: The per-superstep state snapshot (a dict).
        :returns: ``{"mode", "requested", "note"}`` for the frontend.
        """
        mode_data = state.get("mode", {})
        if not isinstance(mode_data, dict):
            mode_data = getattr(mode_data, "model_dump", dict)()
        return {
            "mode": mode_data.get("resolved", "general"),
            "requested": mode_data.get("requested", "general"),
            "note": mode_data.get("note", ""),
        }

    def _update_plan_step_status(
        self, state: KleaAgentState, results: list[CallToolResult]
    ) -> dict[str, Any]:
        """Mark the current plan step done/failed from the tool results.

        Any ``is_error`` result marks the step ``failed``  ---  this mirrors the
        permission + ``isError`` handling in
        ``klea_utils/mcp/dispatch.py:dispatch_tool_calls`` and
        ``klea_utils/nodes/tools_caller.py:ToolsCallerNode``.  Guards against
        empty plans and missing steps.

        :param state: Current graph state.
        :param results: Tool call results (one per call in ``tool_calls``).
        :returns: State updates carrying the updated plan.
        """
        if not state.plan.step_list:
            self.logger.warning("No plan steps to update")
            return {}
        if state.plan.current_step_index >= len(state.plan.step_list):
            self.logger.warning("Plan step index out of range")
            return {"plan": state.plan}
        current_step = state.plan.step_list[state.plan.current_step_index]
        current_step.status = "failed" if any(r.is_error for r in results) else "done"
        state.plan.current_step_index += 1
        return {"plan": state.plan}

    async def _create_graph(self):
        """Create the LangGraph"""
        self.workflow = StateGraph(KleaAgentState, context_schema=KleaRunContext)

        self._init_graph_state_node = InitGraphState(
            logger=self.logger, label="Initializing"
        )
        self.workflow.add_node(
            self._init_graph_state_node.label, self._init_graph_state_node.execute
        )

        # Operating mode (ADR-0030): decide at task entry, before any work.
        # Scientific mode requires an approved curated knowledge source; the
        # agent has none configured yet (``retriever_config``/``stores`` are
        # deferred until the ADR-0029 retrieval phase), so a scientific
        # request routes to the informing node instead of silently downgrading
        # to an unverified answer.
        self._mode_decision_node = ModeDecision(
            logger=self.logger,
            label="Determining mode",
            source_available=(
                self.retriever_config is not None and self.stores is not None
            ),
        )
        self.workflow.add_node(
            self._mode_decision_node.label, self._mode_decision_node.execute
        )
        self._mode_informer_node = ModeInformer(
            logger=self.logger, label="Informing about mode"
        )
        self.workflow.add_node(
            self._mode_informer_node.label, self._mode_informer_node.execute
        )

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

        self._goal_setter_node = GoalSetter(
            logger=self.logger,
            label="Setting goal",
            llm_models=self.llm_models,
            output_schema=GoalSchema,
            memory=False,
        )
        self.workflow.add_node(
            self._goal_setter_node.label, self._goal_setter_node.execute
        )

        self._explore_planner_node = ExplorePlanner(
            logger=self.logger,
            label="Exploring",
            llm_models=self.llm_models,
        )
        self.workflow.add_node(
            self._explore_planner_node.label, self._explore_planner_node.execute
        )

        self._planner_node = Planner(
            logger=self.logger,
            label="Planning",
            llm_models=self.llm_models,
        )
        self._planner_node.set_tools_info(self.tools_info)
        # ToolsPicker/Caller are the shared nodes from ``klea_utils`` (ADR-0020).
        # ``tools_info`` is the per-domain description map built by
        # ``BaseLangGraph._build_tools_info`` before ``_create_graph``; the
        # explicit ``prompt_registry_location`` is required  ---  the shared
        # class would otherwise resolve ``prompts/`` relative to
        # ``klea_utils``.  ``model_type="chat"`` per review (may become a
        # dedicated "reasoning" role later).
        self._tools_picker_node = ToolsPicker(
            logger=self.logger,
            label="Selecting tools",
            llm_models=self.llm_models,
            tools_info=self.tools_info,
            model_type="chat",
            prompt_registry_location=Path(__file__).parent / "nodes" / "prompts",
        )
        self._tools_caller_node = ToolsCallerNode(
            logger=self.logger,
            label="Running tools",
            mcp_client=self.mcp_client,
            tools_meta={t.name: t.meta for t in (self.mcp_tools or []) if t.meta},
            post_dispatch=self._update_plan_step_status,
        )
        self._tools_router_node = ToolsRouter(logger=self.logger, label="Routing tools")
        self._evaluator_node = Evaluator(logger=self.logger, label="Evaluating")
        self._answer_user_node = AnswerUser(
            logger=self.logger, label="Preparing response"
        )
        # Wire nodes  ---  order mirrors ``rag_pkg/klea_rag/rag.py:_create_graph``
        # (guard -> goal -> explore -> picker/caller -> evaluate -> answer).
        # Parallel tool calls are already handled by
        # ``klea_utils/mcp/dispatch.py:dispatch_tool_calls`` via
        # ``asyncio.gather``; the older ToolOrchestrator TODO is kept for
        # context on multi-tool prompt/schema evolution.
        self.workflow.add_node(self._planner_node.label, self._planner_node.execute)
        self.workflow.add_node(
            self._tools_picker_node.label, self._tools_picker_node.execute
        )
        self.workflow.add_node(
            self._tools_caller_node.label, self._tools_caller_node.execute
        )
        # Evaluator: needs to handle failed tool calls and ask the planner to
        # update the plan if required
        self.workflow.add_node(self._evaluator_node.label, self._evaluator_node.execute)
        self.workflow.add_node(
            self._answer_user_node.label, self._answer_user_node.execute
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

        self.workflow.add_edge(START, self._init_graph_state_node.label)
        self.workflow.add_edge(
            self._init_graph_state_node.label, self._mode_decision_node.label
        )
        # ADR-0030: a mode that cannot run (e.g. Scientific without a curated
        # source) is informed, not silently downgraded; proceed otherwise.
        self.workflow.add_conditional_edges(
            self._mode_decision_node.label,
            self._mode_router_node,
            {
                "proceed": self._guard_node.label,
                "inform": self._mode_informer_node.label,
            },
        )
        self.workflow.add_conditional_edges(
            self._guard_node.label,
            self._guard_router_node.execute,
            {
                "safe": self._goal_setter_node.label,
                "unsafe": self._decline_to_answer_node.label,
            },
        )
        self.workflow.add_edge(
            self._goal_setter_node.label, self._explore_planner_node.label
        )
        self.workflow.add_edge(
            self._explore_planner_node.label, self._tools_picker_node.label
        )
        self.workflow.add_edge(self._planner_node.label, self._tools_picker_node.label)
        self.workflow.add_edge(
            self._tools_picker_node.label, self._tools_caller_node.label
        )
        # TODO: we probably need a node here that takes tools output from
        # picker and puts them in the right state field for exploration
        # TODO: we also need some flag that decides whether the next step here
        # should be planning or evaluation. If it's coming off exploration, it
        # needs to go to planning. If it's in the plan, it needs to go to
        # evaluation
        self.workflow.add_conditional_edges(
            self._tools_caller_node.label,
            self._tools_router_node.execute,
            {
                "failed": self._tools_picker_node.label,
                "explored": self._planner_node.label,
                "continue": self._evaluator_node.label,
            },
        )

        self.workflow.add_conditional_edges(
            self._evaluator_node.label,
            self._step_router_node,
            {
                # should never be here
                "not_started": self._planner_node.label,
                # next step
                "in_progress": self._tools_picker_node.label,
                # plan isn't working
                "failed": self._planner_node.label,
                "aborted": self._answer_user_node.label,
                "completed": self._answer_user_node.label,
            },
        )
        if self.memory:
            self.workflow.add_edge(
                self._answer_user_node.label,
                self._summarise_history_node.label,
            )
            self.workflow.add_edge(self._summarise_history_node.label, END)
        else:
            self.workflow.add_edge(self._answer_user_node.label, END)
        self.workflow.add_edge(self._decline_to_answer_node.label, END)
        self.workflow.add_edge(self._mode_informer_node.label, END)

        if self.checkpointer:
            self.graph = self.workflow.compile(checkpointer=self.checkpointer)
        else:
            self.graph = self.workflow.compile()

        self._export_graph_png("klea-agent-lang-graph.png")
