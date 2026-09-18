---
status: "accepted"
date: 2026-08-28
decision-makers: Ankur Sinha
consulted: ""
informed: ""
---

# Configurable fallback when no grounded answer can be generated

## Context and Problem Statement

``Evaluator`` (LLM-judged ``confidence``, ``coverage``, ``groundedness``
etc.) and ``RouteEvaluator`` decide whether a generated answer is good
enough to send to the user.  When retrieval yields weak context (low
coverage or confidence after the configured retrieval attempts) or the
answer is ungrounded/empty, the graph must close out differently than a
grounded answer.  Options are: (a) refuse with "could not find info",
(b) fall back to the chat LLM's training data, (c) return the
best-effort grounded answer with a warning.

Specific score cutoffs and attempt counts are configurable defaults in
``route_evaluator.py`` / ``AppConfig.general`` -- they are not part of
this decision and may be tuned without changing it.

A single fixed policy does not fit all deployments: an academic demo
must be explicit about when it has no evidence, while a general
assistant may be allowed to fall back to its training data when the
curated corpus is silent.

What should the RAG do when no grounded answer can be generated, and
how should users be warned?

## Decision Drivers

* Must not hallucinate on behalf of the corpus: academic validation
  depends on whether the answer came from retrieval or from memory.
* Must remain configurable per deployment (general chat vs curated demo)
  without forking the graph.
* Must surface explanation when the answer is not the best grounded
  answer (best-effort) or is from training data.
* Must compose with ``always retrieve`` (ADR-0008) without letting
  retrieval bypass the evaluator -- ``RouteEvaluator`` is the single
  chokepoint that decides.

## Considered Options

* **A. Always refuse with "could not find info"** -- never use training
  data.  Rejected: useful for strict demo, but wastes a capable fallback
  LLM when the user explicitly allows general chat.
* **B. Always fall back to training data** -- whenever grounded answer
  fails, call ``AnswerGeneral`` with the training-data LLM.  Rejected:
  hides grounding failure; hallucination appears cited.
* **C. Configurable fallback with graded close-out (chosen)** -- introduce
  ``general.fallback_to_training_data`` (bool) and
  ``general.fallback_warning`` (string) per ``rag_pkg/klea_rag/config.py:
  39``.  ``RouteEvaluator`` (``route_evaluator.py:132``) closes out in
  three ways once budgets are exhausted:
  - ``fallback`` -> ``AnswerGeneral`` (uses ``fallback_config`` to prepend
    ``format_alert(fallback_warning)`` only when ``query_domains`` is not
    ``["undefined"]``, i.e. a real domain did match but retrieval still
    failed);
  - ``undefined`` -> ``FixedAnswer`` (``_ask_user_for_clarification_node``)
    "Apologies. I could not answer..." or ``_refuse_answer_node``
    "Sorry. I cannot answer this query as it does not fall into my
    permitted domains...";
  - ``best_effort`` -> ``AnswerUser`` (grounded answer delivered with
    evaluator warning surfaces via ``NodeStreamData``).
  ``AnswerGeneral`` (``klea_utils/nodes/answer_general.py:89``) appends
  ``format_alert(fallback.warning)`` only for domain-routed queries.

* **D. Single string fallback (no warning)** -- variant of C without the
  alert.  Rejected: needs the same routing plus a way to tell the user
  which mode produced the final message.

## Decision Outcome

Chosen option: "C. Configurable fallback with graded close-out".

* ``AppConfig.general`` in ``rag_pkg/klea_rag/config.py:39`` gained
  ``fallback_to_training_data: bool = True`` and
  ``fallback_warning: str = ""`` (plus ``non_domain_chat`` for the
  ``RouteQuery`` non-domain branch).  ``write_config_template`` writes
  them so new deployments see the options.
* Wiring in ``rag_pkg/klea_rag/rag.py:279``:
  ``AnswerGeneral(..., fallback_config=FallbackConfig(enabled=fallback_to_training_data,
  warning=fallback_warning))`` and ``RouteEvaluator(..., fallback_to_training_data=...)``.
* Routing in ``route_evaluator.py:129`` (graded close-out once
  retrieval/rewrite budgets are exhausted; see diagram in
  ``docs/developer-info.rst`` Architecture): low ``coverage``/``confidence``
  routes to ``fallback`` (when enabled) or ``undefined``; ungrounded or
  empty answers route to ``undefined``; otherwise ``best_effort`` (grounded
  but warned).  Cutoffs and attempt budgets are configurable.

  ``fallback`` is the only path that reaches ``AnswerGeneral`` from the
  evaluator loop; ``undefined`` reaches
  ``_ask_user_for_clarification_node``/``_refuse_answer_node``;
  ``best_effort`` reaches ``_answer_user_node``.

* ``AnswerGeneral._update_state`` (``answer_general.py:89``) guards
  the alert:

  ```python
  if fallback and fallback.enabled and fallback.warning:
      if "undefined" not in query_domains:
          answer = format_alert(fallback.warning) + "\n\n" + answer
  ```

  so plain ``non_domain_chat`` answers do not carry the domain-fallback
  warning.

* ``Evaluator`` still emits the graded scores
  (``confidence``/``coverage``/``groundedness``/``relevance``/
  ``coherence``/``conciseness``) and ``next_step``;
  ``RouteEvaluator`` logs the final ``route`` via ``NodeStreamData``
  (``heading: Route Evaluation``, ``details: {route, next_step,
  retrieval_attempts, rewrite_attempts}``) so inspection UIs (ADR-0013)
  can surface whether the final answer was grounded, best-effort, or
  fallback.

### Consequences

* Good, because a strict academic deployment can set
  ``fallback_to_training_data: false`` to force explicit refusals
  ("could not find info"), while a general deployment can opt into
  training-data fallback when the curated corpus is silent.
* Good, because ``fallback_warning`` lets deployments explain the mode
  inline (e.g. "No relevant documents were found -- answering from
  general knowledge") without confusing plain non-domain chat (warning
  suppressed for ``["undefined"]``).
* Good, because evaluator warnings (``best_effort``) stay distinct
  from fallback (training-data) warnings -- the graph has three close-
  out modes, not two, so the answer is never silently hallucinated.
* Bad, because exposing both flags as global ``general.*`` booleans
  is coarser than per-domain fallback policy; a domain that needs both
  modes would require two deployments today.
* Bad, because the ``format_alert`` fallback alert is a single
  string -- richer structured provenance (per-paragraph provenance)
  would need a further ADR.

### Confirmation

* ``utils_pkg/klea_utils/nodes/answer_general.py`` unit coverage for
  ``FallbackConfig`` (enabled true/false, ``query_domains`` ``undefined``
  guard).
* ``rag_pkg/klea_rag/nodes/route_evaluator.py`` routing branches
  exercised via evaluator fixtures (``fallback``/``best_effort``/
  ``undefined`` paths exhausting the configured retrieval/rewrite attempt
  budgets).
* ``docs/troubleshooting.rst`` (vector-store mismatch) and the
  ``Evaluator``/``RouteEvaluator`` ``inspect`` ``NodeStreamData``
  streams show the final ``route`` to the NiceGUI/TUI inspector (proof
  for ADR-0013 inspection).
* ``ty``/``ruff`` clean for the three nodes and ``config.py``; ``docs:
  make html`` still renders the RAG pipeline figure with the three
  close-out edges.

## Pros and Cons of the Options

### Configurable fallback with graded close-out (chosen)

* Good, because deployment decides strict refusal vs training-data
  fallback
* Good, because ``best_effort`` (grounded but warned) stays distinct
  from ``fallback`` (training-data)
* Good, because warning is scoped to domain-routed fallbacks
  (``undefined`` non-domain chat not polluted)
* Bad, because global per-deployment flag (per-domain would be finer)
* Bad, because post-warning provenance is still a single alert string

### Always refuse (no fallback)

* Good, because never hallucinates
* Bad, because wastes useful training-data answers when deployment
  allows them

### Always fallback to training data

* Good, because always answers
* Bad, because hides grounding failure

## More Information

* Code: ``rag_pkg/klea_rag/config.py:39`` (``fallback_to_training_data``,
  ``fallback_warning``), ``klea_utils/nodes/answer_general.py:28`` (``FallbackConfig``
  + warning guard), ``rag_pkg/klea_rag/nodes/evaluator.py`` (scored
  evaluation), ``rag_pkg/klea_rag/nodes/route_evaluator.py:129`` (graded
  close-out), ``rag_pkg/klea_rag/rag.py:279`` (wiring),
  ``klea_utils/llm.py:221`` (``format_alert``), ``klea_utils/nodes/abstract.py``
  (``NodeStreamData`` inspection contract).
* Related: ``ADR-0008`` (always retrieve -- this ADR governs what
  happens when even mandatory retrieval cannot ground an answer);
  ``ADR-0010`` (guard node, which also ends in
  ``_decline_to_answer_node``); ``ADR-0013`` (inspection features that
  surface the ``route`` + ``fallback_warning`` to validate output).
* Decisions codified ``2026-08-28``; underlying wiring predates ADRs
  (``2026-03..04`` ``rag_pkg`` graph extraction, ``2026-08-05..07``
  ``answer_general``/``evaluator`` prompts, ``2026-08-21`` filter
  routing).
