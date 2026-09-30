Orchestrator framework
======================

.. autoclass:: klea_utils.graph.base.BaseLangGraph
   :members:
   :show-inheritance:

Shared state schemas
--------------------

.. automodule:: klea_utils.graph.schemas
   :members:
   :show-inheritance:

Shared base state
-----------------

All application graph states subclass
``klea_utils.graph.state.BaseGraphSchema`` (``utils_pkg/klea_utils/graph/state.py``),
which holds the fields the shared nodes and the streaming layer rely on:
``query``, ``messages``, ``guard_decision``, ``context_summary``,
``summarised_till``, ``message_for_user``, ``tool_calls``, ``tool_results``
and the reduced ``usage_metrics``.  Applications add their own fields (the
agent's ``mode``/``plan``/...; the RAG's ``query_domains`` and reference
material).

..
   ``BaseGraphSchema`` is a Pydantic model whose fields annotate framework
   types (``AnyMessage``, ``CallToolResult``); autodoc cannot build it while
   those packages are mocked (see ``autodoc_mock_imports`` in ``conf.py``),
   so it is documented here in prose.

Reducers
--------

.. automodule:: klea_utils.graph.reducers
   :members:
   :show-inheritance:

Per-run runtime context
-----------------------

.. automodule:: klea_utils.graph.context
   :members:
   :show-inheritance:

..
   LLMModel was moved to klea_utils.llm (see :doc:`llm`) in v0.4.0.
   Keep this comment to avoid re-adding it here.
