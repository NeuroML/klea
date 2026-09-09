---
status: "superseded by ADR-0029"
date: 2026-09-02
decision-makers: Ankur Sinha
consulted: ""
informed: klea contributors
---

# Agent loop topology: plan -> explore -> toolpick -> observe -> evaluate

## Context and Problem Statement

The coding agent must iteratively achieve a natural-language goal
(e.g. ``fix the ingester's OOM on 8000 PDFs``) via plan execution
against the filesystem and MCP tools (bundled ``list_files``/``read``/
``download``, ``nml-mcp`` search/sandbox, per-domain MCP servers).
Industry practice favours a ``flat ReAct loop`` (chuck everything
into context and let the LLM decide what to do next) vs an explicit
graph with dedicated planning, exploration, tool selection,
observation, and evaluation nodes.

The agent design must also decide: (1) whether planning and tool picking
are separate LLM calls for robustness; (2) whether exploration is a
single global scan or two-tiered with ``ready`` gating; (3) whether a
plan step should allow parallel tool calls; (4) whether evaluation
needs a preceding observer, and what happens on repeated failure.

## Decision Drivers

* Each LLM call should do one task for robustness (small ``qwen3:0.6b``
  vs larger models; larger models *could* do both planning and tool
  generation, but separation lets tool outputs be fed to the evaluator).
* Context must not become rigid by design; the loop must loop
  and be able to re-plan when the plan is exhausted.
* Non-determinism: component failures (tool ``is_error:true`` per
  ``ADR-0003``, permission denials per ``ADR-0007``) must be attributable
  to a single file/call, not to a whole batch.
* State must grow to hold the exploration summary without polluting
  ``messages`` (``exploration_summary`` field).

## Considered Options

* **A. Flat ReAct loop (Claude Code / OpenCode style)** -- one LLM call
  per turn that sees the whole context and decides whether to think,
  pick a tool, or declare success.  Rejected: implicitly couples
  planning + tool generation in a single LLM call; no explicit
  ``planner``/``evaluator`` contract, so the pipeline's branching
  (``continue`` / ``best_effort`` / ``fallback``) is not inspectable
  (``ADR-0013``) and the ``_pre_exec`` skip / ``@final execute``
  template (``ADR-0019``) cannot be reused per phase.
* **B. Structured plan -> explore -> toolpick -> observe -> evaluate vs
  flat ReAct (proposed)** -- the topology of ``ADR-0013`` inspection,
  ``ADR-0019`` abstract nodes, and ``ADR-0020`` ``ToolsPicker``/``ToolsCaller``
  is composed as: ``goal_setter`` -> ``planner`` -> ``explore_planner``
  (global, run once, cache, re-run only on major change) -> per-step
  ``explore`` (run each cycle if needed, can replace previous step's
  data, ``ready``-gated: ``explore->planner`` if not ready, otherwise
  ``plan-step -> evaluator``) -> ``tool_picker``/``tool_caller`` (via the
  unified ``ToolsPicker``/``ToolsCallerNode`` + ``dispatch`` + permission
  gate) -> ``observer`` (tool-output–driven retry: success → proceed,
  failure → retry a few times, persistent failure → re-plan via planner
  update) -> ``evaluator`` (where we are in the plan; ``continue``/
  ``best_effort``/``fallback``/``undefined`` per ``ADR-0009``).

  The agent separates ``planner`` (suggest tools) and ``tool_picker``
  (generate calls) so each LLM call does one task for robustness; tool
   outputs are fed to the evaluator together (tool success/failure shapes
  the next plan step).  A plan step allows parallel tool calls by default
  (optimistic), relying on Pydantic validation; if a call fails it is
  isolated and re-picked for only that call.  This design is implemented
  in ``agent_pkg/klea_agent/nodes/``.

## Decision Outcome

*Status: ``superseded by ADR-0029`` -- retained for history. Governing invariants are now in ADR-0029 (Agent correctness architecture: evidence, provenance and verification as architectural objects). Future topology refinements are derived from that ADR, not this one.*

*Previous status ``proposed`` -- the agent graph wiring for this topology was
not yet cemented/accepted.  The current ``devdocs/system/c4-container.md``
shows the ``klea_agent`` container and the ``BaseLangGraph`` family
(``ADR-0016``/``ADR-0019``) as the locus, but the ``plan->explore->
toolpick->observe->evaluate`` loop is described here as a proposed ADR.
When accepted, the graph will implement:

* ``goal_setter`` + ``planner`` (LLM) -> ``explore_planner`` (global,
  cached, re-run on major change) -> per-step ``explore`` (can replace
  previous step's data, ``ready`` state) -> ``tool_picker`` (unified
  ``ToolsPicker`` via ``klea_utils/nodes/tools_picker.py``,
  ``model_type="chat"``) / ``tool_caller`` (unified
  ``ToolsCallerNode`` + ``mcp/dispatch`` permission + ``isError``
  synthesis, see ``ADR-0020``) -> ``observer`` (tool-output observe +
  bounded retry -> ``planner``) -> ``evaluator`` (where-are-we-in-plan;
  ``continue``/``best_effort``/``fallback``/``undefined`` per
  ``ADR-0009``) with ``exploration_summary`` state growth and
  ``_CustomChannelEnabler`` streaming (``ADR-0013``).

The single-responsibility split (planner suggests, picker
generates), global + per-step exploration tiers, parallel-tool
optimism, and observer-driven re-plan are recorded here as the
proposed topology vs the industry flat ReAct alternative.

### Consequences

* Good, because each LLM call does one task (robust for small
  ``qwen3:0.6b``) and tool outputs are fed to the evaluator as a
  batch, so re-planning is evidence-driven, not context-soup driven.
* Good, because the graph is inspectable via ``NodeStreamData``
  (``guard_decision``, ``exploration_summary``, ``tool_calls``,
  ``tool_results``, ``text_response_eval``) rather than only ``thinking``.
* Good, because the observer isolates per-call failures before the
  evaluator re-plans; staged delivery (Stage 1 = run models, Stage 2
  = autonomous synthesis) remains coherent.
* Bad, because the graph has more nodes and conditional edges than a
  flat loop, so tuning branching (when to ``observe`` vs when to
  ``evaluate``, when ``ready`` gates ``explore->planner``) is more
  surface.
* Bad, because the proposed status means the exact edge labels and the
  ``observer`` retry budget are not yet fixed; acceptance criteria
  include closing the ``devdocs/system/c4-container.md:143``
  ``adr/0003-agent-rag-integration`` placeholder (now collides with
  ``0003-mcp-iserror`` and will become ``0025``+).

### Confirmation

* Code: ``agent_pkg/klea_agent/nodes/{planner,explore_planner,goal_setter,
  evaluator,tools_router}.py`` implement the topology as staged;
  ``klea_agent`` is ``WIP`` per ``devdocs/system/c4-container.md:
  51`` (not yet published).  ``klea_rag`` pipeline (ADR-0008) is the
  mature path that the agent will compose with (AAI path deferred).
* ``AGENTS.md`` per-package ``agent_pkg/AGENTS.md`` still defers node
  extraction; this ADR is the forward-linked design to be codified when
  accepted.

## Pros and Cons of the Options

### Structured plan->explore->toolpick->observe->evaluate (proposed)

* Good, because per-LLM-call single responsibility (robust for small models)
* Good, because inspector shows exploration/tool/evaluation steps
* Good, because observer isolates per-call failures before re-plan
* Bad, because more nodes/edges to tune than flat ReAct

### Flat ReAct loop (Claude Code / opencode)

* Good, because flexible (LLM decides next step from context)
* Bad, because planning+tool generation conflated; pipeline is inspectability-poor

## More Information

* Related: ``ADR-0016``/``ADR-0019`` (Template Method at graph + node) for the agent loop.
* Code: ``agent_pkg/klea_agent/nodes/{planner,explore_planner,goal_setter,
  evaluator,tools_router,fix}.py`` (staged), ``utils_pkg/klea_utils/nodes/abstract.py:61``
  (``AbstractLLMNode`` template), ``utils_pkg/klea_utils/nodes/tools_picker.py``/
  ``tools_caller.py`` (unified picker/caller, ADR-0020).
* Related: ``ADR-0016``/``ADR-0019`` (Template Method at graph + node),
  ``ADR-0013`` (inspection), ``ADR-0020`` (picker/caller), ``ADR-0006``
  (monorepo that keeps the agent graph in-repo).
* Superseded: governing decision now ADR-0029; this file retained for the flat-ReAct vs structured-loop evaluation. The `c4-container.md:143` forward ref is now `0029`.
* Former status ``proposed``; would have been ``accepted`` when the agent loop
   wiring was cemented and the ``c4-container.md:143`` forward ref was
   renumbered from ``0003-agent-rag-integration``.
