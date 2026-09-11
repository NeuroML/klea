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

from klea_agent.nodes.answer_from_results import AnswerFromResults
from klea_agent.nodes.answer_user import AnswerUser
from klea_agent.nodes.await_review import AwaitReview
from klea_agent.nodes.init_graph import InitGraphState
from klea_agent.nodes.mode_router import ModeDecision, ModeInformer
from klea_agent.nodes.operational_evaluator import OperationalEvaluator
from klea_agent.nodes.planner import Planner
from klea_agent.nodes.triage_router import (
    TriageRouter,
    current_step_key,
    update_tool_retry_counts,
)

from .config import AppConfig
from .schemas import (
    ArtefactSchema,
    CodeSchema,
    Discovery,
    EvaluationSchema,
    GoalSchema,
    KleaAgentState,
    Mode,
    PlannerOutput,
    PlanSchema,
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
    graph_name = "klea-agent"

    #: Cap on accumulated tool results kept per step (bounds checkpoint and
    #: prompt size); only the most recent results are retained.
    MAX_STEP_RESULTS = 20

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
            PlannerOutput,
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

    async def _planner_router(self, state: KleaAgentState) -> str:
        """Route on the Planner's ``plan.status`` (ADR-0035).

        ``not_needed`` -> answer inline (the Planner wrote the answer);
        ``unplannable`` -> failure answer; ``in_review`` -> human review;
        otherwise (``in_progress``) -> run the plan through the tool work loop.
        """
        status = state.plan.status
        if status == "not_needed":
            return "answer"
        if status == "unplannable":
            return "failure"
        if status == "in_review":
            return "review"
        return "act"

    async def _evaluation_router(self, state: KleaAgentState) -> str:
        """Return the OperationalEvaluator verdict (ADR-0035).

        ``step_incomplete`` / ``step_done`` continue the work loop through the
        picker; ``need_replan`` escalates to the Planner; ``plan_done`` ends at
        the answer and ``abort`` at the failure answer.
        """
        return state.evaluation.next_step

    async def _picker_router(self, state: KleaAgentState) -> str:
        """Route after the tools picker (ADR-0035).

        An empty ``tool_calls`` means the picker found no available tool for the
        current step; the Planner revises the plan rather than the caller
        running nothing (which would loop).  The picker is the authority on
        tool suitability, so the Evaluator is not involved.
        """
        return "dispatch" if state.tool_calls else "replan"

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
        channel, so the frontend can render the active mode, its assurance
        label, and any explanation note.  Full verification/assurance
        enforcement is deferred to the ADR-0029 phase; the label is always
        ``unverified`` until then.

        ``requested`` is included because ``Mode`` is a whole-object
        field (no reducer): after a page reload the web UI's
        ``query_extra`` is empty, so the next query would send
        ``requested=general`` and silently overwrite/ reset the
        checkpointed mode back to general.  Hydration restores
        ``requested`` into the selector, keeping the re-request aligned
        with the user's last intent.

        :param state: The per-superstep state snapshot (a dict).
        :returns: ``{"mode", "requested", "assurance", "note"}`` for the
            frontend.
        """
        mode_data = state.get("mode", {})
        if not isinstance(mode_data, dict):
            mode_data = getattr(mode_data, "model_dump", dict)()
        return {
            "mode": mode_data.get("resolved", "general"),
            "requested": mode_data.get("requested", "general"),
            "assurance": mode_data.get("assurance", "unverified"),
            "note": mode_data.get("note", ""),
        }

    def _record_tool_round(
        self, state: KleaAgentState, results: list[CallToolResult]
    ) -> dict[str, Any]:
        """Record a dispatched tool round: retry counter + per-step outputs.

        Passed as the tool caller's ``post_dispatch`` callback.  A round is one
        ToolsPicker -> ToolsCaller dispatch (it may contain several parallel
        tool calls).  Updates the ADaPT per-step retry counter (a
        conditional-edge router cannot update state, so the counter is
        maintained here and :class:`TriageRouter` reads it to decide retry vs
        replan; incremented on any ``is_error`` result and cleared when the
        round made progress, ADR-0035), appends the round's results to
        ``step_outputs`` so the Evaluator and Planner see every observation for
        the step (bounded to the most recent ``MAX_STEP_RESULTS``), and counts
        the round in ``tool_rounds``.

        :param state: Current graph state.
        :param results: Tool call results (one per call in ``tool_calls``).
        :returns: State updates carrying the updated retry counts and outputs.
        """
        counts = update_tool_retry_counts(state, results)
        step = current_step_key(state)
        outputs = dict(state.step_outputs or {})
        outputs[step] = [*outputs.get(step, []), *results][-self.MAX_STEP_RESULTS :]
        rounds = state.tool_rounds + 1
        self.logger.debug(
            f"{counts = }\n{step = }\n{len(outputs.get(step, [])) = }\n{rounds = }"
        )
        return {
            "tool_retry_counts": counts,
            "step_outputs": outputs,
            "tool_rounds": rounds,
        }

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

        # Single entry brain (ADR-0035): the Planner decides inline answer vs
        # plan, writes the immutable goal, and produces the plan.  It reads
        # conversation history so follow-ups and earlier failed plans inform it.
        self._planner_node = Planner(
            logger=self.logger,
            label="Planning",
            llm_models=self.llm_models,
            memory=self.memory,
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
            post_dispatch=self._record_tool_round,
        )
        self._triage_router_node = TriageRouter(logger=self.logger, label="Triaging")
        self._op_evaluator_node = OperationalEvaluator(
            logger=self.logger,
            label="Evaluating",
            llm_models=self.llm_models,
        )
        self._answer_from_results_node = AnswerFromResults(
            logger=self.logger,
            label="Composing answer",
            llm_models=self.llm_models,
        )
        self._answer_user_node = AnswerUser(
            logger=self.logger, label="Preparing response"
        )
        # Human plan review (ADR-0035): stub until the interrupt/resume stage.
        self._await_review_node = AwaitReview(
            logger=self.logger, label="Awaiting review"
        )
        # Work-loop nodes: the shared picker/caller (ADR-0020) plus the
        # deterministic TriageRouter and the operational OperationalEvaluator
        # (ADR-0035).  Parallel tool calls are handled by
        # ``klea_utils/mcp/dispatch.py:dispatch_tool_calls`` via
        # ``asyncio.gather``.
        self.workflow.add_node(self._planner_node.label, self._planner_node.execute)
        self.workflow.add_node(
            self._tools_picker_node.label, self._tools_picker_node.execute
        )
        self.workflow.add_node(
            self._tools_caller_node.label, self._tools_caller_node.execute
        )
        self.workflow.add_node(
            self._op_evaluator_node.label, self._op_evaluator_node.execute
        )
        self.workflow.add_node(
            self._answer_from_results_node.label,
            self._answer_from_results_node.execute,
        )
        self.workflow.add_node(
            self._answer_user_node.label, self._answer_user_node.execute
        )
        self.workflow.add_node(
            self._await_review_node.label, self._await_review_node.execute
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
                "safe": self._planner_node.label,
                "unsafe": self._decline_to_answer_node.label,
            },
        )
        # Planner entry routing (ADR-0035): route on ``plan.status``.
        self.workflow.add_conditional_edges(
            self._planner_node.label,
            self._planner_router,
            {
                "answer": self._answer_user_node.label,
                "failure": self._answer_from_results_node.label,
                "review": self._await_review_node.label,
                "act": self._tools_picker_node.label,
            },
        )
        # Human review loops back to the Planner, which interprets the
        # feedback and owns the in_review <-> in_progress transition.
        self.workflow.add_edge(self._await_review_node.label, self._planner_node.label)
        # The picker is the authority on tool suitability: if it finds no
        # suitable tool, replan; otherwise dispatch the selected calls.
        self.workflow.add_conditional_edges(
            self._tools_picker_node.label,
            self._picker_router,
            {
                "dispatch": self._tools_caller_node.label,
                "replan": self._planner_node.label,
            },
        )
        self.workflow.add_conditional_edges(
            self._tools_caller_node.label,
            self._triage_router_node.execute,
            {
                "retry": self._tools_picker_node.label,
                "evaluate": self._op_evaluator_node.label,
                "replan": self._planner_node.label,
            },
        )
        self.workflow.add_conditional_edges(
            self._op_evaluator_node.label,
            self._evaluation_router,
            {
                "step_incomplete": self._tools_picker_node.label,
                "step_done": self._tools_picker_node.label,
                "need_replan": self._planner_node.label,
                "plan_done": self._answer_from_results_node.label,
                "abort": self._answer_from_results_node.label,
            },
        )
        # Answer synthesis is separate from evaluation: the Evaluator judges,
        # AnswerFromResults generates the reply, AnswerUser delivers it.
        self.workflow.add_edge(
            self._answer_from_results_node.label, self._answer_user_node.label
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
