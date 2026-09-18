---
status: "accepted"
date: 2026-08-28
decision-makers: Ankur Sinha
consulted: ""
informed: ""
---

# Shared abstract node hierarchy (Template Method for nodes)

## Context and Problem Statement

RAG and the coding agent both need Graph nodes that call LLMs with
prompts, structured schemas, streaming inspection, and state updates.
Early ``rag_pkg`` and ``code_ai`` packages each had their own ``LLMNode``
bases that duplicated: model selection via ``llm_models[model_type]``
(``chat``/``guard``/``plan``), prompt loading (``_load_prompt_file`` +
``_get_system_prompt`` / ``_get_human_prompt``), per-invoke
``RunnableConfig`` building, ``ainvoke`` + ``_process_output`` +
``_update_state``, and the ``_get_inspect``/``_get_status``
inspection contract plus ``write_custom_stream``.  Copy-paste made bug
fixes divergent and streaming events inconsistent.

Like ``BaseLangGraph`` at the orchestrator level (``ADR-0016`` Template
Method), the node level needs a single template that both domains
share.  How should node behaviour be factored?

## Decision Drivers

* DRY across ``klea_rag`` and ``klea_agent`` (and future packages) for
  the ``label``-aware logger, ``execute(state)`` contract,
  ``_pre_exec`` skip, and the ``_CustomChannelEnabler`` streaming
  seam.
* Env-derived ``llm_models`` must remain the single source of truth:
  each node's ``model_type`` (e.g. ``"guard"``, ``"chat"``) keys into
  ``BaseLangGraph.llm_models`` and ``model_defaults`` must be frozen
  (``_ConfigurableModel`` ``configurable_fields="any"`` per-task
  overrides cannot change them).
* Inspection contract must be bounded and typed (``NodeStreamData`` with
  ``heading``/``summary``/``details``/``display``) and uniform for
  web/TUI/SSE (see ``ADR-0013``).
* Router nodes (``add_conditional_edges``) are not LLM nodes and need a
  distinct abstract (``str`` return), but still share the label/streaming
  contract.

## Considered Options

* **A. Per-app node base copy** -- each package keeps its own
  ``BaseLLMNode`` copy.  Rejected: the ``ploggable`` `llm_model`
  container refactor (``95fc002``/``66608b7``) and the ``_pre_exec``
  skip fix (``d288d94``) would have been duplicated across two
  ``LLMNode`` copies.
* **B. Free-function helpers** -- expose ``build_prompt``,
  ``invoke_llm``, ``update_state`` as functions that each node calls.
  Rejected: call order would be re-established per node and the
  ``_last_*`` inspection capture (``_last_prompt``/``_last_output``/
  ``_last_result``/``_token_usage``) would have no single place to
  live.
* **C. Shared abstract hierarchy with Template Method per node type
  (chosen)** -- ``utils_pkg/klea_utils/nodes/abstract.py`` provides
  two layers:
  - ``AbstractLangGraphNode[TSchema,TReturn]`` (ABC) -- ``label``,
    child logger, ``write_custom_stream`` via ``get_stream_writer()``,
    and the shared contract: ``_pre_exec(state) -> bool`` (default
    ``True``), ``_pre_exec_stream`` (``progress``), ``_post_exec_stream``
    (``inspect``/``state``) plus the ``_get_inspect``/``_get_status``
    hooks (all ``None`` by default).
  - ``AbstractLLMNode[TSchema]`` (extends the above,
    ``TReturn = dict[str,Any]``) -- ``model_type`` + ``model_defaults``
    (frozen, per-subclass class attr), ``_llm_entry`` from
    ``llm_models[model_type]``, and a ``@final async def execute``
    template: ``_pre_exec`` skip -> ``_pre_exec_stream`` -> build
    prompt (``_get_system_prompt``/``_get_human_prompt`` ->
    ``_create_prompt_template`` -> ``_get_prompt_variables`` ->
    ``_invoke_prompt``) -> ``_configure_llm`` -> ``_invoke_llm`` (must be
    ``await ainvoke`` for streaming callbacks) -> ``_process_output`` ->
    ``_update_state`` -> ``_extract_usage`` -> ``_update_usage_metrics`` ->
    ``_post_exec_stream`` (plus ``_get_usage`` LLM-specific).
  * Router nodes use a third abstract: ``AbstractRouterNode[TSchema]``
    (``TReturn = str``) with only ``execute(state) -> str``.

  This mirrors ``BaseLangGraph``'s Template Method at a finer grain:
  the orchestrator owns the graph lifecycle; the node base owns the
  per-node LLM invocation lifecycle.

## Decision Outcome

Chosen option: "C. Shared abstract hierarchy with Template Method per node
type in ``klea_utils``".

* Location: ``utils_pkg/klea_utils/nodes/abstract.py:61``
  ``AbstractLangGraphNode`` (generic over ``TSchema``/``TReturn``) +
  ``AbstractLLMNode`` (adds ``model_type``/``model_defaults`` and the LLM
  template) + ``AbstractRouterNode`` (``str`` return, no LLM template).
  ``nodes/base.py`` re-exports concrete helpers
  (``LangGraphNode``/``_ConfigurableModel``) for the RAG/agent
  ``_model`` wiring but the MADR contract lives in ``abstract.py``.
* Every RAG node (``classify_question.py``, ``guard.py``,
  ``generate_retrieval_query.py``, ``retrieve_info.py``,
  ``answer_from_context.py``, ``evaluator.py``) and every agent node
  (``planner.py``, ``explore_planner.py``, ``goal_setter.py``,
  ``evaluator.py``) extends one of the three abstracts and implements
  only its hooks:
  ``_get_prompt_variables``/``_update_state``/``_get_default_error_result``
  (all nodes) and, for LLM nodes, ``_configure_llm``/``_invoke_llm``/
  ``_process_output``/``_invoke_prompt``/``_get_human_prompt``/
  ``_get_system_prompt``/``_create_prompt_template`` (the template
  enforces ``@final execute`` order).
* ``model_type`` is checked against ``llm_models`` keys in
  ``AbstractLLMNode.__init__`` (``KeyError`` with valid keys listed).
  ``model_defaults`` is a frozen per-subclass class attr that user
  ``context`` overrides cannot change (``llm.py:808``
  ``configurable_fields="any"`` still honours the node's pin).
* Inspection: ``AbstractLangGraphNode._pre_exec_stream`` emits
  ``{type:"progress", node:label}`` and ``_post_exec_stream`` emits
  ``inspect``/``state``; ``AbstractLLMNode`` augments it with
  ``_get_usage`` (``TokenUsage`` -> ``NodeStreamData``).  The graph's
  ``_CustomChannelEnabler`` (``required_stream_modes = ("custom",)``)
  is still required so the ``custom`` channel is enabled in
  ``astream_events`` v3.
* ``AbstractLangGraphNode`` is intentionally not a Template Method
  over ``execute`` (LLM vs tool-calling vs router flows differ too
  much).  It only standardises ``_pre_exec``/``_pre_exec_stream``/
  ``_post_exec_stream`` and the ``_get_inspect`` contract;
  ``AbstractLLMNode.execute`` is the true template.  This is the
  "Template Method at two levels" companion to ``ADR-0016`` (graph) and
  lives together with it in the todolist as requested.

### Consequences

* Good, because new nodes are one decision: ``guard.py``,
  ``classify_question.py``, and ``explore_planner.py`` all share the
  same ``label``/logger/streaming contract without copy-paste.
* Good, because ``@final execute`` fixes the prompt -> invoke ->
  process -> update -> usage -> stream order; adding
  ``_CustomChannelEnabler`` or ``NodeStreamData`` handling happens
  once (see ``ADR-0013`` inspection).
* Good, because ``model_type``/``model_defaults`` keep the per-role
  ``LLMModel`` table (ADR-0016) as the single source for which model a
  node may use, without per-node env plumbing.
* Bad (inherent to pattern): inheritance couples every node to
  ``klea_utils/nodes/abstract.py`` -- a template change (e.g. the
  ``BaseMessage``-object memory switch in ``c6e1a8a``) touches every
  node.  The three abstracts keep the seam explicit, but the template
  is rigid.  One cannot have Template Method without this rigidity;
  hooks (``_pre_exec``/``_pre_exec_stream``/``_post_exec_stream`` per
  node type) are the intended extension points.
* Bad, because ``AbstractLangGraphNode.execute`` is not itself a
  template (router vs LLM divergence) -- tool-calling nodes still
  manage their own ``execute`` flow and only share the streaming
  hooks, so some heterogeneity remains.

### Confirmation

* ``ty`` extra-paths for all four packages still resolve the
  ``Abstract*Node`` generics; ``ruff`` clean for ``abstract.py``/
  ``base.py``.
* ``mcp_pkg: pytest -v`` + ``utils_pkg: pytest -m "not localonly"``
  exercise the shared path (``guard`` skip via ``_pre_exec`` returning
  ``False`` -> empty dict).
* Manual: ``ClassifyQuestion._get_inspect`` / ``GuardNode._pre_exec`` skip
  still emit the correct ``NodeStreamData`` via the shared base.

## Pros and Cons of the Options

### Shared abstract hierarchy with Template Method per node type (chosen)

* Good, because DRY node contract with single ``@final execute``
  per LLM node type
* Good, because ``model_type``/``model_defaults`` keep the
  ``llm_models`` table single source
* Bad (inherent to pattern): template is rigid at two levels (graph +
  node) -- one cannot have Template Method without this rigidity;
  hooks are the extension points

### Per-app node base copy

* Good, because minimal abstraction
* Bad, because ``95fc002``/``66608b7``/``c6e1a8a`` churn duplicated

## More Information

* Code: ``utils_pkg/klea_utils/nodes/abstract.py:61``
  (``AbstractLangGraphNode``/``AbstractLLMNode``/``AbstractRouterNode``),
  ``nodes/base.py`` (``LangGraphNode``/``_ConfigurableModel`` helpers),
  ``graph/base.py:49`` ``_CustomChannelEnabler``,
  ``api/sse.py`` (SSE seam).
* Related: ``ADR-0016`` (graph-level Template Method -- this ADR is the
  node-level companion; both use Template Method and are intentionally
  kept separate as requested); ``ADR-0013`` (inspection signals owned
  by the node base); ``AGENTS.md:139`` ``BaseLangGraph`` pointer.
* Commits: ``7e88ba1`` (move common methods to abstract graph node),
  ``95fc002``/``66608b7`` (``LLMModel`` container), ``c6e1a8a``/``f5bcfde``
  (tool-caller abstract share), ``d288d94`` (guard ``_pre_exec`` skip
  semantics).
* Generic parameters (amended ``2026-09-10``): ``AbstractLangGraphNode[TState,
  TReturn]`` (state, return), ``AbstractLLMNode[TState, TOutput]`` (state,
  structured-output schema) and ``BaseLLMNode[TState, TOutput]``.  App-specific
  nodes parameterize ``TState`` with their concrete state (``RAGState`` /
  ``KleaAgentState``); shared ``klea_utils`` nodes use ``BaseModel``.  This
  supersedes the earlier single-parameter LLM form, where the output schema was
  forwarded as the parent's *state* parameter, so state was never parameterized
  and app nodes had to narrow ``BaseModel`` in overrides.
* Codified ``2026-08-28``; abstract hierarchy extracted ``2026-08-16``
  and hardened with ``2026-07-21..29`` ``model container`` work.
