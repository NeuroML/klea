---
status: "accepted"
date: 2026-08-28
decision-makers: Ankur Sinha
consulted: ""
informed: ""
---

# Unified shared tool picker/caller for agent and RAG

## Context and Problem Statement

``klea_rag`` and ``klea_agent`` both need an LLM tool-selection step
(``ToolsPicker``) and a tool-execution step (``ToolsCaller`` via MCP).
Originally each package had its own copy: ``rag_pkg/klea_rag/nodes/
tools_caller.py`` and ``agent_pkg/klea_agent/nodes/tools_caller.py``
with slightly different ``ToolCallSchema``/``ToolResult`` handling,
different ``check_tool_arguments_permissions`` wiring, and duplicated
``dispatch_tool_calls`` + ``textualize_tool_results`` logic.  A fix in
one (e.g. ``checkpaths`` gating or ``is_error:true`` synthetic results)
had to be ported manually to the other, and the two pickers could drift
in prompt registry location or ``model_type`` handling.

Should tool picking/calling stay per-package or be unified into the
shared ``klea_utils`` framework?

## Decision Drivers

* DRY: ``BaseLangGraph`` already owns ``llm_models`` Single Source Of Truth (SSOT), MCP
  ``Client``/``tools_info``, and the ``QueryDomainSchema`` generation;
  per-domain ``tools_info: dict[str, dict[str, ToolInfo]]`` plus
  ``mcp_tools: list[Tool]`` are graph-wide state in the base.
* Permission gate must be single source: ``check_tool_arguments_
  permissions`` (author-side ``checkpaths``) + ``dispatch_tool_calls``
  synthetic ``is_error:true`` must be exercised by both graphs.
* ``ToolCallSchema``/``ToolCallsSchema`` (``klea_utils.mcp.schemas``)
  and the ``tool_calls: list[ToolCallSchema]`` / ``tool_results:
  list[CallToolResult]`` state fields are shared by both ``RAGState``
  and ``KleaAgentState``; the picker/caller must write those fields
  idiomatically.
* The picker is LLM-bound (``model_type="chat"``, prompt registry
  ``Path(__file__).parent / "nodes" / "prompts"`` per app) while the
  caller is transport-bound (``mcp_client``, ``tools_meta`` map, plus
  an optional ``post_dispatch`` hook for the agent's per-plan-step
  status).

## Considered Options

* **A. Keep per-package picker/caller copies** -- each app maintains
  its own ``tools_picker.py``/``tools_caller.py``.  Rejected: duplicated
  ``dispatch_tool_calls`` + ``checkpaths`` + ``textualize`` wiring;
  ``BaseLangGraph._build_tools_info`` divergence between ``rag`` and
  ``agent`` would be hidden; ``ToolCallSchema`` drift.
* **B. Free-function helpers only** -- expose ``pick_tools(tools_info)``
  and ``call_tools(calls, mcp_client)`` as helpers that each graph node
  calls.  Rejected: per-graph node wiring (prompt registry location,
  ``model_type``, ``post_dispatch``) would still be per-app, and the
  graph node contract (``label``, ``_get_inspect`` streaming
  per ADR-0013) would not be shared.
* **C. Unified shared nodes plus ``dispatch`` helper (chosen)** --
  ``klea_utils/nodes/tools_picker.py`` (``ToolsPicker`` as an
  ``AbstractLLMNode`` with per-app ``prompt_registry_location`` +
  ``model_type="chat"``) and ``klea_utils/nodes/tools_caller.py``
  (``ToolsCallerNode`` as an ``AbstractLangGraphNode``, with
  ``mcp_client``, ``tools_meta: dict[str, dict]`` (the ``Tool.meta``
  map from ``BaseLangGraph.mcp_tools``), and an optional agent-only
  ``post_dispatch`` callback).  Shared ``klea_utils/mcp/schemas.py``
  (``ToolCallSchema``/``ToolCallsSchema``/``ToolInfo``) and
  ``klea_utils/mcp/dispatch.py`` (``dispatch_tool_calls`` + synthetic
  ``CallToolResult(is_error=True)`` via ``to_result`` + permission
  gate) are reused by both ``RAG`` and ``KleaAgent``.  The base graph
  (ADR-0016) and node hierarchy (ADR-0019) stay the DRY lifecycle owners;
  the picker is the LLM-template node, the caller the router/transport
  node.

## Decision Outcome

Chosen option: "C. Unified shared ``ToolsPicker``/``ToolsCallerNode``
plus ``klea_utils.mcp`` shared schemas/dispatch".

* Location: ``utils_pkg/klea_utils/nodes/tools_picker.py`` ``ToolsPicker``
  (``model_type="chat"``) + ``utils_pkg/klea_utils/nodes/tools_caller.py``
  ``ToolsCallerNode`` (``mcp_client``, ``tools_meta``, ``post_dispatch``)
  + ``utils_pkg/klea_utils/mcp/schemas.py`` (``ToolCallSchema``/
  ``ToolCallsSchema``/``ToolInfo``) + ``utils_pkg/klea_utils/mcp/
  dispatch.py`` (``dispatch_tool_calls`` + ``check_tool_arguments_
  permissions`` + ``textualize_tool_results``/``build_tool_description``
  in ``klea_utils/tools.py``).  ``klea_utils/mcp/server/bundled.py``
  + ``graph/base.py:490`` ``_bundled_server_config`` remain the MCP
  wiring (ADR-0004); this ADR only factors the picker/caller nodes.
* Graph wiring: ``rag_pkg/klea_rag/rag.py:256`` ``ToolsPicker(tools_info=
  self.tools_info, prompt_registry_location=...)`` + ``ToolsCallerNode(
  mcp_client=self.mcp_client, tools_meta={t.name: t.meta ...},
  post_dispatch=None)``; ``agent_pkg/klea_agent/klea_agent.py`` passes a
  ``post_dispatch`` callback to mark per-plan-step status.  Both write the
  same ``tool_calls`` / ``tool_results`` fields consumed by the same
  inspection events (``ADR-0013`` ``_get_inspect``).
* Permission integration (ADR-0007): ``ToolsCallerNode`` calls
  ``dispatch_tool_calls`` which runs ``check_tool_arguments_permissions``
  on every call **before** it reaches the MCP server; denied calls never
  reach the server and become synthetic non-halting error results
  (``is_error:true`` via ``tool_result.to_result``) so the LLM can
  adapt.  The gate runs in the shared node, so both RAG and agent are
  protected without per-app porting.
* ``BaseLangGraph`` (ADR-0016) owns the ``mcp_tools`` -> ``tools_info``
  + ``domain_mcp_configs`` mapping and the ``mcp_client`` lifecycle;
  the abstract node hierarchy (ADR-0019) owns the ``@final execute``
  template and streaming; this ADR only factors the two nodes that sit
  on top of both.

### Consequences

* Good, because a single ``ToolsCallerNode`` fix (e.g. ``checkpaths``
  gating or ``is_error:true`` synthesis) applies to both RAG and agent
  without drift.
* Good, because ``ToolCallSchema``/``ToolCallsSchema`` and the
  ``tool_calls``/``tool_results`` state fields are a single source;
  per-domain ``tools_info`` mapping lives once in the base graph.
* Good, because the picker remains per-app configurable (prompt dir,
  ``model_type``) while the caller remains transport-fixed; the
  agent's ``post_dispatch`` hook is the only per-app divergence.
* Bad, because shared nodes couple both graphs to
  ``klea_utils/nodes/{tools_picker,tools_caller}.py`` -- a picker
  prompt change touches RAG and agent.
* Bad, because ``tools_meta`` (the ``Tool.meta`` map) is derived from
  ``BaseLangGraph.mcp_tools`` at ``setup`` time; a hot-reloaded MCP
  server that adds tools at runtime will not be seen until the next
  ``setup``.

### Confirmation

* ``utils_pkg/tests/test_nodes_tools_picker.py`` + ``test_tools_caller.py``
  exercise both nodes via the shared path (``tools_info`` from
  ``BaseLangGraph``); ``mcp_pkg/tests/test_tool_tags.py:22`` tag vocab
  still routes through the shared ``register_tools``.
* ``ty`` ``extra-paths`` for ``klea_utils.mcp.schemas`` still resolve
  cross-package; ``ruff`` clean for ``tools_picker.py``/``tools_caller.py``
  and ``mcp/dispatch.py``.
* Live: ``klea-rag cli`` and ``klea`` (agent) both list the same
  ``bundled`` + ``nml-mcp`` tools per domain via the shared picker,
  and denied path calls via the shared caller surface as synthetic
  ``ToolResult(is_error=True)`` before the MCP server is invoked.

## Pros and Cons of the Options

### Unified shared picker/caller + dispatch (chosen)

* Good, because single permission + ``is_error`` dispatch for both graphs
* Good, because shared ``ToolCallSchema``/state fields keep picker/caller
  idiomatic
* Bad, because picker prompt change touches both graphs

### Per-package copies

* Good, because minimal abstraction
* Bad, because drift, porting cost, and hidden base-graph divergence

## More Information

* Code: ``utils_pkg/klea_utils/nodes/tools_picker.py``,
  ``nodes/tools_caller.py``, ``mcp/schemas.py`` (``ToolCallSchema``/
  ``ToolCallsSchema``/``ToolInfo``), ``mcp/dispatch.py`` (``dispatch_tool_calls``),
  ``graph/base.py:70`` (``BaseLangGraph`` owns ``tools_info``/``mcp_tools``),
  ``nodes/abstract.py:61`` (node base for both).
* Related: ``ADR-0004`` (bundled stdio that this caller consumes),
  ``ADR-0007`` (permission gate this caller runs), ``ADR-0016``
  (``BaseLangGraph`` template), ``ADR-0019`` (node hierarchy), ``ADR-0013``
  (inspection stream that shows picker/caller ``inspect``).
* Commits: ``7e88ba1`` (move common methods to abstract node),
  ``95fc002``/``66608b7`` (``LLMModel`` container), ``c6e1a8a``/``f5bcfde``
  (tool-caller abstract share), ``d288d94`` (guard skip), plus the
  per-package ``rag_pkg/klea_rag/nodes/tools_caller.py`` ->
  ``utils_pkg/klea_utils/nodes/tools_caller.py`` extraction (hardened
  ``2026-08-17``).
* Codified ``2026-08-28``; unified nodes extracted ``2026-08-16..17``
  alongside the abstract node hierarchy.
