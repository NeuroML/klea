---
status: "accepted"
date: 2026-08-28
decision-makers: Ankur Sinha
consulted: ""
informed: ""
---

# Structured message memory for nodes

## Context and Problem Statement

RAG and agent graphs keep conversation history in ``RAGState.messages``
/ ``KleaAgentState.messages`` and inject it into LLM prompts.
Early Klea flattened history into a single string appended to the system
prompt (``add_memory_to_prompt(context_summary)`` or ``str(messages)``).

This loses role information: the prompt template cannot distinguish
``HumanMessage`` vs ``AIMessage`` vs ``SystemMessage``, so the model
sees history as narrative text rather than a turn-taking transcript.
Summarisation also becomes lossy because the summariser's input is the
same flattened string, and the ``num_history_chars`` window cannot be
exposed as real turns to the ``ChatPromptTemplate``.

Should nodes store and inject history as typed messages or as strings?

## Decision Drivers

* The LLM's chat API is turn-based (``HumanMessage``/``AIMessage``
  sequence), not a single string; fidelity improves adherence to the
  transcript, especially for ``memory=True`` nodes
  (``ClassifyQuestion``/``AnswerGeneral``/``GenerateRetrievalQuery``).
* ``ChatPromptTemplate`` can interleave a system-side list
  ``[("system", text), *recent_messages, ("human", human_prompt)]``;
  a flattened string forces the whole history into the system block.
* Summarisation (``SummariseMemoryNode``) needs the structured summary
  ``context_summary`` plus the verbatim recent window
  ``get_recent_messages(..., num_history_chars)`` without losing
  speaker attribution.
* ``_get_system_prompt`` must remain overridable per node (some nodes
  disable memory entirely; e.g. ``AnswerFromContext`` ``memory=False``).

## Considered Options

* **A. String memory only** -- ``BaseLLMNode`` injects ``context_summary``
  via ``add_memory_to_prompt`` into the system text; callers pass
  ``messages`` only as a state field.  Rejected: history is flattened to
  ``str``; ``ChatPromptTemplate`` cannot place turns between system and
  human; speaker role is lost after ``str()``.
* **B. Hybrid: summary string + typed recent window (chosen)** -- history
  lives as ``list[BaseMessage]`` in ``state.messages``; ``BaseLLMNode``
  keeps two hooks: ``_get_memory_addition(state)`` (default:
  ``add_memory_to_prompt(context_summary)`` for nodes that want a single
  string) and ``_get_recent_memory_messages(state)`` (bounded by
  ``num_history_chars`` via ``klea_utils.llm.get_recent_messages``).
  When ``memory=True``, ``BaseLLMNode._get_system_prompt`` returns a
  **list** ``[("system", system_text + schema), *recent_messages]`` where
  ``system_text`` already contains ``context_summary`` and the structured
  schema prompt; ``_create_prompt_template`` then builds
  ``ChatPromptTemplate([*system_messages, ("human", human_prompt)])``.
  When ``memory=False`` it returns a plain string.  This keeps the
  system/human split and the recency guarantee (schema last) while the
  recent window stays typed.
* **C. Full LangGraph ``messages`` checkpoint** -- delegate all memory to
  LangGraph's ``InMemorySaver``/``AsyncSqliteSaver`` messages state.
  Rejected: Klea's ``context_summary`` summarisation threshold
  (``e371a81`` characters, not tokens) and the ``num_history_chars``
  verbatim window are domain-specific and belong to the node, not to
  the checkpointer.

## Decision Outcome

Chosen option: "B. Hybrid: summary string + typed recent window".

* ``utils_pkg/klea_utils/nodes/base.py:702`` ``BaseLLMNode._get_system_prompt``:
  when ``memory`` is true, returns ``list[("system", system_prompt),
  *recent_messages]`` (``recent_messages: list[BaseMessage]`` via
  ``_get_recent_memory_messages``); otherwise returns a string.  Both
  are accepted by ``_create_prompt_template`` (``system_prompt:
  str | list[Any]``).
* ``_get_recent_memory_messages`` (``base.py:739``) is bounded by
  ``self.num_history_chars`` (default ``10_000``) via
  ``klea_utils.llm.get_recent_messages``; it preserves human/ai order
  and speaker role.
* ``_get_memory_addition`` (``base.py:807``) appends the structured
  summary ``context_summary`` for nodes that need it; it is the
  default path for ``memory=True`` nodes that do not override.
* Commit ``c6e1a8a feat(nodes): update memory to include message objects
  rather than string representations`` introduced the ``BaseMessage``
  path; ``e371a81`` switched the summarisation threshold to characters;
  ``f78c410``/``98c6501`` moved the helper to ``klea_utils.llm`` and
  to ``nodes/base.py``.  ``AbstractLLMNode`` template (ADR-0019)
  still owns the per-node ``@final execute`` (pre-check -> prompt ->
  invoke) so the memory seam is a single override point.

### Consequences

* Good, because prompts are turn-faithful: the recent window is typed
  ``HumanMessage``/``AIMessage`` between system and human, not a lossy
  ``str`` inside system.
* Good, because summarisation can consume both the structured
  ``context_summary`` and the verbatim window without losing
  attribution.
* Good, because ``memory=False`` nodes (``AnswerFromContext``,
  ``Evaluator``) opt out cleanly by returning a string; the same base
  serves both.
* Bad, because ``_get_system_prompt`` now returns ``str | list`` and
  ``_create_prompt_template`` must handle both -- a union that
  ``ty`` checks but callers must not assume is a string.
* Bad, because ``num_history_chars`` is a char budget, not a token
  budget; a window of ``10k`` chars is a heuristic whose token cost
  varies by embedding/model.

### Confirmation

* ``utils_pkg/tests/test_nodes_memory.py`` covers both modes via
  ``_dummy_node`` (``memory=True``/``False``) and asserts the
  ``NodeStreamData`` shapes for summarised prompts.
* ``ty`` extra-paths for ``BaseMessage`` + ``Pydantic`` ``BaseModel``;
  ``ruff`` clean for ``nodes/base.py``; ``docs: make html`` still
  renders the RAG pipeline figure (the prompt block now lists the
  interleaved history).
* Manual: ``ClassifyQuestion`` with ``memory=True`` logs
  ``system_messages = [("system", text), HumanMessage(...),
  AIMessage(...)]`` before ``_invoke_prompt``; ``memory=False`` logs a
  plain string.

## Pros and Cons of the Options

### Hybrid: summary + typed recent window (chosen)

* Good, because turn-faithful prompts via ``ChatPromptTemplate``
* Good, because summarisation consumes both summary and verbatim window
* Good, because ``memory=False`` opt-out is trivial
* Bad, because ``str | list`` return type must be handled

### String memory only

* Good, because single string path
* Bad, because role information lost; ``ChatPromptTemplate`` cannot
  interleave turns

## More Information

* Code: ``utils_pkg/klea_utils/nodes/base.py:702``
  (``_get_system_prompt``), ``base.py:739``
  (``_get_recent_memory_messages``), ``base.py:807``
  (``_get_memory_addition``), ``klea_utils/llm.py`` (``get_recent_messages``,
  ``add_memory_to_prompt``), ``nodes/abstract.py:218``
  (``AbstractLLMNode`` template), ``nodes/summarise_memory.py``
  (summarisation threshold ``e371a81``).
* Related: ``ADR-0019`` (abstract node hierarchy this memory seam is
  part of), ``ADR-0016`` (``BaseLangGraph`` that runs the nodes that
  use this memory), ``ADR-0013`` (inspection stream that shows the
  memory in the NiceGUI 3-column inspector).
* Commits: ``c6e1a8a`` (``BaseMessage`` path), ``e371a81`` (char
  threshold), ``98c6501``/``f78c410`` (helper moves).
* Codified ``2026-08-28``; memory seam hardened in ``2026-08-16``
  alongside the abstract node extraction.

## Update (2026-10-20): two memory streams and a bounded recent window

The agent now separates user-visible conversation from internal run history:

* ``messages`` holds user-visible turns (query, HITL notes, final answer).
* ``messages_internal`` holds plan versions, evaluator verdicts and reasoning
  conclusions.  It is checkpointed for provenance but is never injected
  verbatim into a node prompt.
* ``SummariseMemoryNode`` consumes both streams and maintains
  ``summarised_till`` / ``summarised_internal_till``.  Internal history is
  summarised from its cursor to the end; user-visible history keeps the
  existing recent-window exclusion.
* ``_get_recent_memory_messages`` now uses the bounded
  ``DEFAULT_RECENT_MEMORY_CHARS`` window promised by this ADR, instead of the
  regressed ``summarised_till - 2``-to-end slice.  Long runs therefore inject
  ``context_summary`` plus the recent window, not the whole run.
* RAG has no internal run history: it keeps the single ``messages`` stream,
  and the shared summariser treats the absent ``messages_internal`` as empty.
