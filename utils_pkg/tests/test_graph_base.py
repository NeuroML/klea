#!/usr/bin/env python3
"""
Test BaseLangGraph execution methods with a toy graph.

File: tests/test_graph_base.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail DOT com>
"""

import logging
from typing import Any, cast, override

import pytest
from fastmcp.mcp_config import MCPConfig
from klea_utils.graph.base import BaseLangGraph
from klea_utils.graph.context import KleaRunContext
from klea_utils.llm import LLMModel, create_configurable_model
from klea_utils.nodes.answer_general import AnswerGeneral
from klea_utils.nodes.fixed_answer import FixedAnswer
from langchain_core.messages import AnyMessage
from mcp.types import Tool, ToolAnnotations
from pydantic import BaseModel, Field


class ToyState(BaseModel):
    """Minimal state for the toy graph."""

    query: str = ""
    message_for_user: str = ""
    messages: list[AnyMessage] = Field(default_factory=list)
    context_summary: str = ""


class ToyGraph(BaseLangGraph):
    """Minimal graph: AnswerGeneral (LLM) -> FixedAnswer (non-LLM) -> END."""

    env_class: type[BaseModel] = BaseModel
    config_class: type[BaseModel] = BaseModel
    env_var: str = "TOY_ENV_FILE"
    env_file_default: str = "toy.env"
    graph_name: str = "ToyGraph"

    def __init__(self):
        super().__init__(logging_level=logging.INFO, checkpoint="none", log_file=False)
        from platformdirs import PlatformDirs

        self.paths = PlatformDirs(self.graph_name.lower())
        self.logger = logging.getLogger(self.graph_name)

    @override
    def _load_env(self) -> None:
        """No-op: skip env file loading, provide minimal app_env/app_config.

        ``setup()`` calls ``_apply_model_names`` and
        ``_apply_provider_defaults`` after ``_load_env``, so the no-op must
        still provide the objects those helpers read from.
        """
        from types import SimpleNamespace
        from typing import cast

        from pydantic import BaseModel

        # ``app_env`` is typed ``BaseModel`` on the base class; the generated
        # settings instance is not available since ``_load_env`` is a no-op.
        self.app_env = cast(BaseModel, SimpleNamespace(chat_model="ollama:qwen3:0.6b"))
        self.app_config = cast(BaseModel, SimpleNamespace(providers={}))

    @override
    def _configure_resources(self) -> None:
        pass

    @override
    def _setup_models(self) -> None:
        model = create_configurable_model(logger=self.logger)
        self.llm_models = {
            "chat": LLMModel(
                instance=model,
                model_name="ollama:qwen3:0.6b",
            ),
        }

    @override
    async def _create_graph(self) -> None:
        from langgraph.graph import END, START, StateGraph

        workflow = StateGraph(ToyState)  # ty: ignore[invalid-assignment]

        self._answer_node = AnswerGeneral(
            logger=self.logger,
            label="Saying hello",
            llm_models=self.llm_models,
            memory=False,
        )
        workflow.add_node(self._answer_node.label, self._answer_node.execute)

        self._fixed_node = FixedAnswer(
            logger=self.logger,
            label="Fixed answer",
            state_attr="message_for_user",
            message="This is the fixed answer.",
        )
        workflow.add_node(self._fixed_node.label, self._fixed_node.execute)

        workflow.add_edge(START, self._answer_node.label)
        workflow.add_edge(self._answer_node.label, self._fixed_node.label)
        workflow.add_edge(self._fixed_node.label, END)

        self.graph = workflow.compile()


class TestBuildToolsInfoAnnotations:
    """_build_tools_info propagates MCP annotations onto ToolInfo (ADR-0037)."""

    @staticmethod
    def _tool(
        name: str,
        *,
        read_only: bool | None = None,
        destructive: bool | None = None,
    ) -> Tool:
        annotations = None
        if read_only is not None or destructive is not None:
            annotations = ToolAnnotations(
                readOnlyHint=read_only, destructiveHint=destructive
            )
        return Tool(
            name=name,
            description=f"{name} tool",
            inputSchema={},
            annotations=annotations,
        )

    def test_annotation_propagation(self):
        graph = ToyGraph()
        graph.mcp_tools = [
            self._tool("search", read_only=True),
            self._tool("delete", destructive=True),
            self._tool("plain"),
        ]
        graph.domain_mcp_configs = {
            "code": MCPConfig(mcpServers={"srv": {"url": "http://example.invalid/mcp"}})
        }

        graph._build_tools_info()

        info = graph.tools_info["code"]
        assert info["search"].read_only is True
        assert info["search"].destructive is None
        assert info["delete"].destructive is True
        assert info["delete"].read_only is None
        # An unannotated tool carries no capability signal.
        assert info["plain"].read_only is None
        assert info["plain"].destructive is None


class WarningGraph(ToyGraph):
    """ToyGraph with required model roles and empty model names.

    ``_setup_models`` declares the model roles with their ``required``
    flags, matching how real graphs declare ``llm_models``.
    """

    env_prefix = "TOY_"

    def _setup_models(self):
        model = create_configurable_model(logger=self.logger)
        self.llm_models = {
            "chat": LLMModel(instance=model, model_name="", required=True),
            "plan": LLMModel(instance=model, model_name="", required=True),
            "guard": LLMModel(instance=model, model_name="", required=False),
        }


class TestCheckRequiredModels:
    """Tests for the startup missing-model warning."""

    def test_warns_for_missing_models(self, caplog):
        graph = WarningGraph()
        graph._setup_models()

        with caplog.at_level(logging.WARNING, logger=graph.graph_name):
            graph._check_required_models()

        assert "have not been set: chat, plan" in caplog.text
        assert "have not been set: chat, plan, guard" not in caplog.text
        assert "TOY_CHAT_MODEL=<not set>" in caplog.text
        assert "TOY_PLAN_MODEL=<not set>" in caplog.text
        # Optional roles still appear in the current-state listing.
        assert "TOY_GUARD_MODEL=<not set>" in caplog.text

    def test_no_warning_when_required_models_set(self, caplog):
        graph = WarningGraph()
        graph._setup_models()
        instance = graph.llm_models["chat"].instance
        graph.llm_models["chat"] = LLMModel(
            instance=instance, model_name="ollama:qwen3:0.6b", required=True
        )
        graph.llm_models["plan"] = LLMModel(
            instance=instance, model_name="ollama:qwen3:0.6b", required=True
        )
        # guard stays empty but is optional -- must not trigger a warning.

        with caplog.at_level(logging.WARNING, logger=graph.graph_name):
            graph._check_required_models()

        assert "have not been set" not in caplog.text


class TestGraphLoggingLevel:
    """BaseLangGraph resolves its console logging level from env/flag > arg."""

    def _make_bare_graph(self):
        """A minimal BaseLangGraph subclass that skips heavy setup."""
        from types import SimpleNamespace

        class BareGraph(BaseLangGraph):
            env_class: type[BaseModel] = BaseModel
            config_class: type[BaseModel] = BaseModel
            env_var: str = "BARE_ENV_FILE"
            env_file_default: str = "bare.env"
            graph_name: str = "BareGraph"
            paths = SimpleNamespace(user_data_dir="/tmp/bare")

            def _configure_resources(self):
                pass

            def _setup_models(self):
                pass

            async def _create_graph(self):
                pass

        return BareGraph(log_file=False, checkpoint="none")

    def test_default_level_is_info(self, monkeypatch):
        """Without KLEA_LOG_LEVEL the default constructor level is INFO."""
        seen = {}

        def fake_setup(app_name, stderr_level=logging.INFO, **kwargs):
            seen["level"] = stderr_level

        monkeypatch.setattr("klea_utils.plogging.setup_root_logger", fake_setup)
        monkeypatch.delenv("KLEA_LOG_LEVEL", raising=False)
        self._make_bare_graph()
        assert seen.get("level") == logging.INFO

    def test_env_debug_overrides_constructor_level(self, monkeypatch):
        """KLEA_LOG_LEVEL=debug forces DEBUG regardless of the passed level."""
        seen = {}

        def fake_setup(app_name, stderr_level=logging.INFO, **kwargs):
            seen["level"] = stderr_level

        monkeypatch.setattr("klea_utils.plogging.setup_root_logger", fake_setup)
        monkeypatch.setenv("KLEA_LOG_LEVEL", "debug")
        self._make_bare_graph()
        assert seen.get("level") == logging.DEBUG

    def test_explicit_constructor_level_wins_when_env_unset(self, monkeypatch):
        """Without KLEA_LOG_LEVEL, an explicit logging_level is honored."""
        seen = {}

        def fake_setup(app_name, stderr_level=logging.INFO, **kwargs):
            seen["level"] = stderr_level

        monkeypatch.setattr("klea_utils.plogging.setup_root_logger", fake_setup)
        monkeypatch.delenv("KLEA_LOG_LEVEL", raising=False)
        from types import SimpleNamespace

        class WarningGraph(BaseLangGraph):
            env_class: type[BaseModel] = BaseModel
            config_class: type[BaseModel] = BaseModel
            env_var: str = "WARN_ENV_FILE"
            env_file_default: str = "warn.env"
            graph_name: str = "WarningGraph"
            paths = SimpleNamespace(user_data_dir="/tmp/warn")

            def _configure_resources(self):
                pass

            def _setup_models(self):
                pass

            async def _create_graph(self):
                pass

        WarningGraph(logging_level=logging.WARNING, log_file=False, checkpoint="none")
        assert seen.get("level") == logging.WARNING


class TestGraphBase:
    """Test all BaseLangGraph execution methods."""

    def setup_method(self):
        self.logger = logging.getLogger("test_graph_base")

    async def _make_graph(self):
        """Build a ToyGraph, skipping if ollama is unavailable."""
        try:
            graph = ToyGraph()
            await graph.setup()
            self.logger.info("ToyGraph setup complete")
            return graph
        except (ConnectionError, AssertionError) as e:
            self.logger.warning(f"Ollama unavailable, skipping: {e}")
            pytest.skip(str(e))

    @pytest.mark.localonly
    async def test_run_graph_invoke(self):
        """Test basic invoke returns a string."""
        toy_graph = await self._make_graph()
        self.logger.info("Testing run_graph_invoke")
        query = "Say 'hi' for testing."
        self.logger.info(f"{query = }")

        result = await toy_graph.run_graph_invoke(query)
        self.logger.info(f"Got result: {result!r}")

        assert isinstance(result, str)
        assert len(result) > 0
        assert result == "This is the fixed answer."
        self.logger.info("run_graph_invoke test passed")

    @pytest.mark.localonly
    async def test_run_graph_invoke_state(self):
        """Test invoke with full state dict."""
        toy_graph = await self._make_graph()
        self.logger.info("Testing run_graph_invoke_state")

        state = {"query": "Say 'hi' for testing."}
        self.logger.info(f"Initial {state = }")

        result = await toy_graph.run_graph_invoke_state(state)
        self.logger.info(f"Final state keys: {list(result.keys())}")
        self.logger.info(f"Final {result = }")

        assert isinstance(result, dict)
        assert "message_for_user" in result
        assert result["message_for_user"] == "This is the fixed answer."
        self.logger.info("run_graph_invoke_state test passed")

    @pytest.mark.localonly
    async def test_run_graph_stream(self):
        """Test astream yields message_for_user strings."""
        toy_graph = await self._make_graph()
        self.logger.info("Testing run_graph_stream")

        messages = []
        async for msg in toy_graph.run_graph_stream("Say 'hi' for testing."):
            self.logger.info(f"Streamed message: {msg!r}")
            messages.append(msg)

        self.logger.info(f"Got {len(messages)} streamed messages")

        assert len(messages) >= 1
        assert "This is the fixed answer." in messages
        self.logger.info("run_graph_stream test passed")

    @pytest.mark.localonly
    async def test_run_graph_astream_events(self):
        """Test astream_events v3 yields progress, token, and complete events.

        Event flow for the toy graph (AnswerGeneral -> FixedAnswer):

          1. AnswerGeneral (LLM node) calls ``write_custom_stream()`` at
             the top of ``execute()``, emitting a progress event via the
             ``custom`` channel.

          2. Token chunks from AnswerGeneral are yielded as ``type="token"``
             events (from the ``messages`` channel).

          3. FixedAnswer (non-LLM node) also calls ``write_custom_stream()``,
             emitting another progress event via the ``custom`` channel.

          4. After the graph completes, the final state is read from the
             ``values`` channel and yielded as ``type="complete"``.
             ``values`` channel and yielded as ``type="complete"``.
        """
        toy_graph = await self._make_graph()
        self.logger.info("Testing run_graph_astream_events")

        events = []
        async for event in toy_graph.run_graph_astream_events("Say 'hi' for testing."):
            self.logger.info(f"Event: {event}")
            events.append(event)

        self.logger.info(f"Got {len(events)} total events")

        progress_events = [e for e in events if e.get("type") == "progress"]
        token_events = [e for e in events if e.get("type") == "token"]
        complete_events = [e for e in events if e.get("type") == "complete"]

        self.logger.info(
            f"Event breakdown: "
            f"{len(progress_events)} progress, "
            f"{len(token_events)} token, "
            f"{len(complete_events)} complete"
        )

        self.logger.info(f"{progress_events = }")
        self.logger.info(f"{token_events = }")
        self.logger.info(f"{complete_events = }")

        assert len(events) >= 2

        progress_nodes = [e["node"] for e in progress_events]
        self.logger.info(f"Progress nodes: {progress_nodes}")

        # Both nodes appear via the custom channel (write_custom_stream).
        # "Saying hello" is emitted by AbstractLLMNode.execute() in its
        # template method; "Fixed answer" is emitted in its own execute().
        # Every node with a label now emits via the custom channel, so the
        # messages channel is only used for token content.
        assert "Saying hello" in progress_nodes
        assert "Fixed answer" in progress_nodes

        assert len(complete_events) == 1
        self.logger.info(f"Complete event: {complete_events[0]}")
        assert complete_events[0]["message_for_user"] == "This is the fixed answer."

        for te in token_events:
            assert "content" in te
            assert "node" in te
            assert isinstance(te["content"], str)

        self.logger.info("run_graph_astream_events test passed")


class ContextToyState(BaseModel):
    """Minimal state for the context-event forwarding graph."""

    query: str = ""
    message_for_user: str = ""
    mode: str = ""
    assurance: str = ""


class ContextGraph(BaseLangGraph):
    """Graph whose context event is *derived from state* (ADR-0032).

    ``context_snapshot`` projects state into a ``context`` event; one node
    writes the state fields and another tries to author a ``custom``
    ``context`` event -- the latter must NOT be forwarded, proving the
    separation is structural.
    """

    env_class: type[BaseModel] = BaseModel
    config_class: type[BaseModel] = BaseModel
    env_var: str = "CTX_ENV_FILE"
    env_file_default: str = "ctx.env"
    graph_name: str = "ContextGraph"

    def __init__(self):
        super().__init__(logging_level=logging.INFO, checkpoint="none", log_file=False)
        from platformdirs import PlatformDirs

        self.paths = PlatformDirs(self.graph_name.lower())
        self.logger = logging.getLogger(self.graph_name)

    @override
    def _load_env(self) -> None:
        from types import SimpleNamespace
        from typing import cast

        self.app_env = cast(BaseModel, SimpleNamespace(chat_model="dummy"))
        self.app_config = cast(BaseModel, SimpleNamespace(providers={}))

    @override
    def _configure_resources(self) -> None:
        pass

    @override
    def _setup_models(self) -> None:
        self.llm_models = {}

    @override
    def context_snapshot(self, state: dict[str, Any]) -> dict[str, Any] | None:
        """Project the state fields into the session context."""
        if not state.get("mode"):
            return None
        return {"mode": state.get("mode"), "assurance": state.get("assurance")}

    @override
    async def _create_graph(self) -> None:
        from langgraph.config import get_stream_writer
        from langgraph.graph import END, START, StateGraph

        workflow = StateGraph(ContextToyState)

        async def _set_mode(state):
            # Normal node behaviour: write the context fields as state.
            return {"mode": "general", "assurance": "unverified"}

        async def _hack_context(state):
            # Attempt to author a context event from a node.  Under ADR-0032
            # this custom-channel event must NOT be forwarded.
            get_stream_writer()({"type": "context", "data": {"hacked": True}})
            return {"message_for_user": "done"}

        workflow.add_node("Set mode", _set_mode)
        workflow.add_node("Hack context", _hack_context)
        workflow.add_edge(START, "Set mode")
        workflow.add_edge("Set mode", "Hack context")
        workflow.add_edge("Hack context", END)
        self.graph = workflow.compile()


class TestContextEventForwarding:
    """Graph-level ``context`` events are derived from state, not nodes."""

    def setup_method(self):
        self.logger = logging.getLogger("test_graph_base.context")

    async def test_context_event_derived_from_state(self):
        graph = ContextGraph()
        await graph.setup()

        events = []
        async for event in graph.run_graph_astream_events("hello"):
            self.logger.debug(f"event: {event}")
            events.append(event)

        context_events = [e for e in events if e.get("type") == "context"]
        assert context_events, "expected the context event to be forwarded"
        # Exactly the state projection, change-deduped; the node-authored
        # "hacked" custom event is structurally dropped.
        assert context_events == [
            {"type": "context", "data": {"mode": "general", "assurance": "unverified"}}
        ]
        assert all("hacked" not in e.get("data", {}) for e in context_events)
        assert [e for e in events if e.get("type") == "complete"], "expected complete"


class _CaptureCompiled:
    """Fake compiled graph that records the ``context`` kwarg per run call."""

    def __init__(self):
        self.calls: list[tuple[str, dict]] = []

    async def ainvoke(self, *args, **kwargs):
        self.calls.append(("ainvoke", kwargs))
        return {"message_for_user": "ok"}

    async def astream(self, *args, **kwargs):
        self.calls.append(("astream", kwargs))
        yield {"node": {"message_for_user": "ok"}}

    async def astream_events(self, *args, **kwargs):
        self.calls.append(("astream_events", kwargs))
        return self._empty()

    async def _empty(self):
        if False:  # pragma: no cover
            yield


class _ContextCaptureGraph(BaseLangGraph):
    """Minimal graph wrapping a ``_CaptureCompiled`` (no setup / no LLM)."""

    env_class: type[BaseModel] = BaseModel
    config_class: type[BaseModel] = BaseModel
    env_var: str = "TOY_ENV_FILE"
    env_file_default: str = "toy.env"
    graph_name: str = "ContextCaptureGraph"

    def __init__(self, compiled: _CaptureCompiled):
        super().__init__(logging_level=logging.INFO, checkpoint="none", log_file=False)
        self.logger = logging.getLogger(self.graph_name)
        self.graph = cast(Any, compiled)

    @override
    def _configure_resources(self) -> None:
        pass

    @override
    def _setup_models(self) -> None:
        self.llm_models = {}

    @override
    async def _create_graph(self) -> None:
        pass


class TestRunContextForwarding:
    """The four query run methods forward the ADR-0033 context verbatim."""

    def setup_method(self):
        self.logger = logging.getLogger("test_graph_base.context-forwarding")

    async def test_run_graph_invoke_forwards_context(self):
        compiled = _CaptureCompiled()
        graph = _ContextCaptureGraph(compiled)
        ctx = KleaRunContext(model_overrides={"chat": {"model": "x"}})

        result = await graph.run_graph_invoke("q", "t", context=ctx)
        assert result == "ok"
        method, kwargs = compiled.calls[-1]
        assert method == "ainvoke"
        assert kwargs["context"] is ctx

    async def test_run_graph_invoke_default_context_none(self):
        compiled = _CaptureCompiled()
        graph = _ContextCaptureGraph(compiled)

        await graph.run_graph_invoke("q", "t")
        method, kwargs = compiled.calls[-1]
        assert method == "ainvoke"
        assert kwargs["context"] is None

    async def test_run_graph_stream_forwards_context(self):
        compiled = _CaptureCompiled()
        graph = _ContextCaptureGraph(compiled)
        ctx = KleaRunContext(model_overrides={"chat": {"model": "x"}})

        msgs = [m async for m in graph.run_graph_stream("q", "t", context=ctx)]
        assert msgs == ["ok"]
        method, kwargs = compiled.calls[-1]
        assert method == "astream"
        assert kwargs["context"] is ctx

    async def test_graph_stream_forwards_context(self):
        compiled = _CaptureCompiled()
        graph = _ContextCaptureGraph(compiled)
        ctx = KleaRunContext(model_overrides={"chat": {"model": "x"}})

        gen = await graph.graph_stream("q", "t", context=ctx)
        chunks = [c async for c in gen]
        assert chunks == [{"node": {"message_for_user": "ok"}}]
        method, kwargs = compiled.calls[-1]
        assert method == "astream"
        assert kwargs["context"] is ctx

    async def test_run_graph_astream_events_forwards_context(self):
        compiled = _CaptureCompiled()
        graph = _ContextCaptureGraph(compiled)
        ctx = KleaRunContext(model_overrides={"chat": {"model": "x"}})

        events = [
            e async for e in graph.run_graph_astream_events("q", "t", context=ctx)
        ]
        # The empty fake event stream still produces the terminal event.
        assert [e for e in events if e.get("type") == "complete"]
        method, kwargs = compiled.calls[-1]
        assert method == "astream_events"
        assert kwargs["context"] is ctx
        assert kwargs["version"] == "v3"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
