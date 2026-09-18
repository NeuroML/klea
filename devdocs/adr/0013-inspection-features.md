---
status: "accepted"
date: 2026-08-28
decision-makers: Ankur Sinha
consulted: ""
informed: ""
---

# Inspection features for validating execution

> **Amended by ADR-0040 (2026-09-18):** the `info` event was removed and
> `debug` renamed to `inspect` (so `_get_info`/`_get_debug` collapsed into a
> single `_get_inspect`).  The `custom` channel now also carries `tool` events
> for chat-renderable output; `NodeStreamData` gained `key`/`preformatted`; and
> `token` is opt-in.  The text below is kept as the historical record; see
> ADR-0040 for the current contract.

## Context and Problem Statement

Most agentic coding tools today show little about how they function
beyond the LLM's ``thinking`` trace.  For a coding agent that is often
sufficient: the user validates by inspecting generated files or test
results.  It is not clear that any current tools have inspection
features beyond that.

Klea -- RAG and agent -- operates in an academic context where the
user must validate the system's work: whether retrieval was grounded,
which documents were cited, what filters and evaluations drove the
answer, and whether the guard or fallback paths were taken.  These
inspection features are not RAG-specific; they are part of the general
Klea framework (``BaseLangGraph``, ``BaseLLMNode``, ``NodeStreamData``
in ``klea_utils``) and serve both the agent and the RAG.  A single
answer string plus ``thinking`` does not convey the graph's internal
state.

Should Klea be thinking-only (like the coding agents) or should the
general framework surface structured inspection signals so users can
validate and trust its output?

## Decision Drivers

* Trust in an academic context depends on provenance: which store(s)
  were queried, which chunks were retrieved, what the retrieval scores
  and filters were, and whether the answer was grounded in them --
  whether via the RAG or the agent's tool calls.
* Debugging depends on stepwise visibility: did the guard classify
  ``safe`` or ``unsafe``; did ``ClassifyQuestion``/planner pick one or
  many domains/plans; did ``RetrieveInfoNode``/tool calls hit
  ``vector_stores`` vs ``bm25_stores``; did ``Evaluator`` judge the
  answer as ``continue``, ``best_effort``, or ``fallback``.
* Coding-agent thinking is not the same contract: an agent's
  ``thinking`` shows planning/plan-execution; Klea's provenance (RAG
  and agent) shows retrieval citations + evaluation/plan signals.  The
  two audiences overlap but academic validation has stricter
  requirements.
* Must compose with the general Klea framework
  (``klea_utils.graph.base.BaseLangGraph`` streaming) and the existing
  web/TUI clients (NiceGUI 3-column, Textual REPL) without forking UIs;
  not a RAG-only concern.

## Considered Options

* **A. Thinking-only (coding-agent style)** -- stream only the LLM
  token chunks (``thinking`` / final answer).  Rejected for Klea: the
  user cannot tell whether the answer was grounded, which documents
  support it, or why a fallback/clarification was taken.
* **B. Raw event log** -- dump every LangGraph ``astream_events``
  ``messages``/``values``/``debug`` event to the UI.  Rejected: too
  verbose for end users; ``messages`` channel interleaves
  ``content-block-delta`` tokens with system/user prompt dumps, and
  ``values`` exposes the full mutable state (including unchecked
  ``_source_scores`` and private metadata).
* **C. Structured per-node inspection signals (chosen)** -- each node
  emits a small, typed ``NodeStreamData`` on three channels:
  ``progress`` (``{type: "progress", node: "<label>"}`` via
  ``write_custom_stream`` as the graph enters the node),
  ``info`` / ``debug`` / ``state`` / ``usage`` (typed summaries
  after execution), plus ``token`` chunks from the LLM nodes'
  ``messages`` channel and ``complete`` with ``message_for_user``.
  The graph uses a dedicated ``_CustomChannelEnabler``
  (``required_stream_modes = ("custom",)``) so the ``custom`` channel
  is always enabled in ``astream_events`` v3.

## Decision Outcome

Chosen option: "C. Structured per-node inspection signals".

* Node contract: ``utils_pkg/klea_utils/nodes/abstract.py`` defines
  ``NodeStreamData`` / ``NodeStreamEvent`` and ``BaseLLMNode`` adds
  ``_get_info`` (one-line summary + ``details`` dict, shown in the UI
  inspector by default), ``_get_debug`` (``info`` plus
  ``input_prompt``/``unprocessed_output``/``processed_output``), and
  ``_get_usage`` (input/output/total tokens).  Nodes override these
  per role:

  * ``GuardNode`` / ``ClassifyQuestion``: ``query_domains``,
    ``classified_domains`` + ``available_domains``.
  * ``GenerateRetrievalQuery``: ``search_query`` + ``filters``.
  * ``ToolsPicker``/``ToolsCallerNode``: ``ToolCallSchema`` selection
    + ``CallToolResult.is_error`` routing (``success_count``).
  * ``RetrieveInfoNode``: per-domain retrieved document list with
    ``_source_scores`` (vector ``[0,1]`` vs BM25 raw) and
    ``reference_material`` size (``max_refs_size``).
  * ``Evaluator``/``RouteEvaluator``: ``confidence``/``coverage``/
    ``groundedness`` scores, ``next_step``, and final ``route``
    (``continue``/``best_effort``/``fallback``/``undefined``) as
    documented in ``ADR-0009``.

  Each ``execute`` emits ``write_custom_stream`` events:

  ```python
  yield {"type": "progress", "node": self.label}
  yield {"type": "inspect", "node": self.label, "data": self._get_inspect().model_dump()}
  ```

* Graph streaming: ``utils_pkg/klea_utils/graph/base.py:700``
  ``BaseLangGraph.run_graph_astream_events`` wraps
  ``graph.astream_events(..., version="v3", transformers=[_CustomChannelEnabler])``
  and yields a normalised stream of ``{type: "progress"|"inspect"|"state"|"usage"|"tool"|"token"|"context"|"complete"}``
  dicts.  ``graph/base.py:49`` ``_CustomChannelEnabler`` is the minimal
  ``StreamTransformer`` that declares the ``custom`` channel so
  ``write_custom_stream`` events flow through (LangGraph v3 only emits
  declared channels).  ``graph_stream`` / ``run_graph_astream_events``
  are the single streaming seam used by both ``klea-rag-serve`` SSE
  (``klea_utils/api/sse.py``) and direct clients.

* UI: web (NiceGUI 3-column with an inspector pane) and TUI REPL both
  consume the same event stream: ``progress`` drives the step indicator,
  ``inspect`` populates the per-node inspector (summary shown, ``details``
  collapsed), ``state`` fills the status pane, ``tool`` renders chat output,
  ``usage`` surfaces per-node token counts for cost introspection, and
  ``token`` streams free-text nodes only (opt-in).  The CLI
  ``klea-rag web`` / ``klea web`` auto-spawns the server and connects
  over SSE.

* Framework-wide validation is the primary consumer (RAG and agent):
  a reviewer can see *which* documents grounded the answer, *how* they
  were retrieved and re-ranked (RRF + recency per ``ADR-0012``), and *why*
  the answer closed as ``best_effort`` vs ``fallback`` (``ADR-0009``)
  without relying solely on the LLM's ``thinking``.  The same stream
  serves agent plan/execution inspection.

### Consequences

* Good, because an academic user can validate provenance: retrieval
  citations, per-domain store hits, ``_source_scores``, filter routing,
  and the evaluator's ``route`` are all inspectable -- not hidden
  behind ``thinking``.
* Good, because debugging is stepwise: ``guard_decision`` (ADR-0010),
  ``classified_domains`` (ADR-0011), per-store ``k``/``max_refs_size``,
  and hybrid ``vector vs BM25`` contributions are surfaced without
  dumping raw ``values`` state.
* Good, because the contract is typed (``NodeStreamData``) and bounded:
  ``info`` is a one-line summary; ``debug`` is opt-in, so the default
  inspector stays concise while power users can expand.
* Bad, because each node now has an ``_get_info``/``_get_debug``
  implementation to maintain (alongside ``_get_prompt_variables``/
  ``_update_state``).  The graph's event volume grows with node count,
  but the per-event payload is small and ``progress``/``info`` are
  indexed, not the full state.
* Bad, because ``token``/``custom`` vs ``messages``/``values`` channel
  semantics are LangGraph v3 specific (``_CustomChannelEnabler`` is
  required) -- callers that use ``astream``/``ainvoke`` without
  ``astream_events(v3, transformers=[_CustomChannelEnabler])`` will not
  see the same events.

### Confirmation

* ``rag_pkg/klea_rag/nodes/classify_question.py:142`` + ``guard.py``,
  ``retrieve_info.py``, ``evaluator.py``/``route_evaluator.py`` all
  implement ``_get_info``/``_get_debug``; ``utils_pkg/tests/test_nodes_*.py``
  asserts the ``NodeStreamData`` shapes.
* ``docs: make html`` still renders the ``rag-lang-graph.png`` with the
  same edge labels; web inspector + TUI REPL both consume
  ``run_graph_astream_events`` and show ``progress``/``info``/``debug``
  per node in local and HuggingFace (``klea-rag-serve`` SSE) deployments.
* Live: ``klea-rag web`` inspector pane already shows
  ``Question Classification → classified_domains`` / ``Retrieving
  information → reference_material`` / ``Answer Evaluation → scores/next_step``
  before the final answer (the ``thinking`` is not the sole validation
  path).
* ``ty`` extra-paths still resolve ``BaseLangGraph`` + node bases;
  ``ruff`` clean for ``graph/base.py:49`` transformer and all node
  modules.

## Pros and Cons of the Options

### Structured per-node inspection signals (chosen)

* Good, because provenance is inspectable (citations, scores, route)
* Good, because stepwise debugging is bounded (``info`` vs ``debug``)
* Good, because the contract is typed (``NodeStreamData``) and works
  for web/TUI/SSE uniformly
* Bad, because each node must maintain ``_get_info``/``_get_debug``
* Bad, because LangGraph v3 ``custom`` channel requires the dedicated
  enabler

### Thinking-only (coding-agent style)

* Good, because minimal events
* Bad, because no grounding/fallback/evaluator provenance for academic
  validation

### Raw event log

* Good, because nothing hidden
* Bad, because verbose and exposes internal state not meant for users

## More Information

* Streaming core: ``utils_pkg/klea_utils/graph/base.py:49``
  ``_CustomChannelEnabler`` + ``graph/base.py:700``
  ``run_graph_astream_events`` (v3 ``messages``/``custom``/``values``
  normalisation), ``klea_utils/nodes/abstract.py`` (``NodeStreamData``/
  ``NodeStreamEvent``, ``BaseLLMNode`` ``_get_inspect``),
  ``klea_utils/api/sse.py`` (SSE), ``klea_utils/graph/base.py`` worker
  ``write_custom_stream`` use.
* Nodes with rich inspection: ``classify_question.py``
  (``classified_domains``), ``guard.py`` (``guard_decision``),
  ``retrieve_info.py`` (per-domain docs + ``_source_scores``),
  ``evaluator.py``/``route_evaluator.py`` (``scores``/``next_step``/
  ``route``), ``generate_retrieval_query.py`` (``search_query``/
  ``filters``).
* Related: ``ADR-0008`` (always retrieve -- what is inspected by
  ``RetrieveInfoNode``), ``ADR-0009`` (fallback vs ``best_effort``/``undefined``
  route shown by ``RouteEvaluator``), ``ADR-0010`` (guard decision
  before retrieval), ``ADR-0011`` (multiple domains surfaced by
  ``ClassifyQuestion``), ``ADR-0012`` (vector vs BM25 ``_source_scores``
  preserved as debug context).
* Decisions codified ``2026-08-28``; inspection wiring grew alongside
  the RAG graph extraction (early ``2026-03..04``) and the NiceGUI
  3-column inspector (``2026-07..08``).
