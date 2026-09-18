---
status: "accepted"
date: 2026-08-28
decision-makers: Ankur Sinha
consulted: ""
informed: ""
---

# Multiple query domains per RAG query

## Context and Problem Statement

Klea RAG routes a user query to one or more configured domains
(`AppConfig.domains`, each with its own vector/BM25 stores and
``description`` plus ``filter_fields``).  Early RAG relied on a
``ClassifyQuestion`` that emitted a single domain: the evaluator loop
was the only path that could cause a second, different domain to be
considered.  For cross-cutting questions (e.g. a query that spans a
methods domain and a literature domain) this meant a good answer
required an extra ``evaluate -> classify`` cycle, and the first answer
was often incomplete.

Should classification emit one domain or several, and how should
retrieval query all of them?

## Decision Drivers

* Answer quality: cross-cutting queries should be answered from all
  relevant domains in the first retrieval pass, not after a failed
  evaluation.
* Must not pollute domains: a store that does not belong to a domain
  must not be queried (silent ``path``/``name`` mismatches are already
  the main troubleshooting cost -- see ``docs/troubleshooting.rst``).
* Must compose with ``always retrieve`` (ADR-0008) and with per-domain
  ``filter_fields`` routing (each domain's retrieval should see only
  its declared fields).
* Must keep the graph topology simple (one ``RouteQuery`` fan-out,
  not per-domain branching).

## Considered Options

* **A. Single query domain, evaluator loop for a second (old)** -- ``ClassifyQuestion``
  emits one ``query_domains[0]``; if ``Evaluator`` judges coverage
  insufficient it routes ``modify_query`` / ``retrieve_more_info`` and
  the query may be reclassified.  Rejected: incurs a full
  answer->evaluate->classify->retrieve cycle before the second domain's
  evidence is available; the first answer often cites only one domain.
* **B. Upfront multiple query domains per LLM call (chosen)** -- ``ClassifyQuestion``
  (``rag_pkg/klea_rag/nodes/classify_question.py:119``) receives
  ``QueryDomainSchema`` whose ``query_domains`` is ``list[Literal[tuple(domains)]]
  `` (built from ``RetrieverConfig.domains.keys()`` plus ``"undefined"`` in
  ``utils_pkg/klea_utils/graph/base.py:347``); the classifier prompt
  (``_build_domain_str``) lists every domain description plus an
  ``undefined`` fallback.  ``_update_state`` validates against
  ``self.domains`` keys, defaults to ``["undefined"]`` on no valid
  match, and strips ``"undefined"`` when a real domain also appears.
  ``RouteQuery`` then routes a single list ``query_domains`` through
  the graph; ``GenerateRetrievalQuery`` + ``RetrieveInfoNode`` iterate
  over all listed domains and their stores in one pass.
* **C. Parallel per-domain graphs** -- one sub-graph per domain, answers
  merged.  Rejected: duplicates ``RetrieveInfoNode``/``AnswerFromContext``
  wiring per domain and complicates ``max_refs_size`` budgeting (now a
  single global budget across domains in RRF order).

## Decision Outcome

Chosen option: "B. Upfront multiple query domains per LLM call".

* Classifier schema (``utils_pkg/klea_utils/graph/base.py:347``)
  ``QueryDomainSchema = create_model(..., query_domains=list[Literal[tuple(
  all_domains)]])`` where ``all_domains = stores.domains.keys() +
  ["undefined"]``.  The prompt block ``## Domains`` is the domain table
  (``ClassifyQuestion._build_domain_str``) plus schema last for recency.
* Validation in ``classify_question.py:119``:

  ```python
  valid = [d for d in result.query_domains if d in self.domains]
  if not valid:
      valid = ["undefined"]
  if len(valid) > 1 and "undefined" in valid:
      valid.remove("undefined")
  ```

  ``RouteQuery`` receives ``query_domains`` as a list and takes a single
  ``domain_query`` edge to the splitter; ``non_domain_query`` (all
  ``["undefined"]``) still routes to ``AnswerGeneral``.
* ``GenerateRetrievalQuery`` (``generate_retrieval_query.py``) generates
  one retrieval query (``search_query`` + ``filters``) whose
  ``config_filters`` are then scoped per domain via ``restrict_metadata_filter``
  (``klea_utils/stores/filters.py``) so each domain only sees the filter
  clauses on its declared ``filter_fields``.  ``RetrieveInfoNode`` loops
  ``for domain, stores in retrievers`` and fuses vector + BM25 hits per
  domain before the global ``max_refs_size`` truncation.

### Consequences

* Good, because cross-cutting queries cite evidence from all relevant
  domains in the first pass; the evaluation loop is now for
  ``best_effort`` / fallback, not for discovering a second domain.
* Good, because the graph stays single-branch (one ``RouteQuery``,
  one splitter, one ``RetrieveInfoNode``) -- per-domain store isolation
  is handled by ``retrieval`` helpers, not graph topology.
* Good, because ``"undefined"`` stripping makes the classifier tolerant
  of an LLM that hedges by emitting both a real domain and ``undefined``;
  single-domain behaviour is preserved when only one domain qualifies.
* Bad, because the classifier prompt grows with the number of domains
  (``## Domains`` block length is ``O(domains)``) and the output space
  is the powerset ``P(domains)``.  A small chat model (small ``qwen3:0.6b``
  etc.) is more likely to confuse domains as the description block
  lengthens and recency dilutes: it may invent a domain not in
  ``self.domains`` (stripped to ``["undefined"]`` and routed to
  ``AnswerGeneral``/``FixedAnswer`` -- loss of recall), hedge by emitting
  ``["DomainA", "undefined"]`` (tolerated but still incurs the stripping
  step), or over-classify (emit a valid but irrelevant domain that passes
  validation and triggers extra ``GenerateRetrievalQuery`` /
  ``RetrieveInfoNode`` lookups).  Under-classification (omitted domain)
  loses citation; false-positive domains cost ``k`` vector+BM25 retrievals
  per stray domain (scoped by ``restrict_metadata_filter`` and bounded by
  the global ``max_refs_size`` budget but not zero).  This is mitigated
  by per-domain filter scoping, the ``max_refs_size`` cap, and
  ``docs/troubleshooting.rst`` ``name``/``path`` hygiene, but the
  ``O(domains)`` prompt + powerset output remain a small-model
  confusion risk.
* Bad, because a query that genuinely spans 3+ domains still pays
  retrievals for all of them even if one domain's evidence would have
  sufficed; over-classification cost is bounded by ``k``/``max_refs_size``
  but not zero.

### Confirmation

* ``classify_question.py:136`` + ``QueryDomainSchema`` still passes
  ``rag_pkg/tests/test_classify_question.py`` (single vs multi-domain
  plus ``undefined`` fallback).
* ``retrieve_info.py`` per-domain ``restrict_metadata_filter`` routing
  verified: cross-domain queries with filters only apply the declared
  fields per domain (contamination previously caused ``0`` results with
  ``k*3`` + native filter).
* ``docs: make html`` still renders the 6-stage figure with the single
  ``domain_query`` edge; ``ty`` cross-package ``RetrieverConfig`` via
  ``ty.toml`` extra-paths is satisfied.
* Live: ``klea-rag-serve`` logs ``classified_domains`` via
  ``ClassifyQuestion._get_inspect`` ``NodeStreamData`` (inspection, ADR-0013)
  already surface multiple domains before ``RetrieveInfoNode`` runs.

## Pros and Cons of the Options

### Upfront multiple query domains per LLM call (chosen)

* Good, because cross-cutting queries are answered in one retrieval pass
* Good, because graph stays single-branch (store isolation in helpers)
* Good, because ``undefined`` stripping tolerates hedging LLMs
* Bad, because classifier prompt grows as ``O(domains)`` and output is
  powerset ``P(domains)``; small models may invent a non-existent
  domain (recovers to ``undefined``), hedge (tolerated), or
  over-classify (false-positive domain -> extra retrievals) while
  under-classification loses citation.  See Consequences for the full
  confusion taxonomy (bounded by ``restrict_metadata_filter`` scoping +
  ``max_refs_size``).

### Single query domain, evaluator loop for a second

* Good, because prompt/output stay tiny (single literal)
* Bad, because requires a full answer->evaluate->re-retrieve cycle to
  incorporate a second domain; first answer often incomplete

## More Information

* Code: ``utils_pkg/klea_utils/graph/base.py:347`` (``QueryDomainSchema``),
  ``rag_pkg/klea_rag/nodes/classify_question.py:119`` (validation),
  ``rag_pkg/klea_rag/nodes/generate_retrieval_query.py`` + ``klea_utils/stores/filters.py``
  (``restrict_metadata_filter``), ``rag_pkg/klea_rag/nodes/retrieve_info.py``
  (per-domain loop + RRF + ``max_refs_size``), ``rag_pkg/klea_rag/rag.py:191``
  (``ClassifyQuestion -> RouteQuery -> _splitter_label``).
* Related: ``ADR-0008`` (always retrieve -- this ADR governs *how many*
  domains that retrieval spans); ``ADR-0009`` (fallback vs
  ``undefined`` non-domain); ``ADR-0012`` (BM25 exact matches that
  benefit most from multi-domain citation); ``docs/troubleshooting.rst``
  (store ``name``/``path`` mismatch).
* Decisions codified ``2026-08-28``; classifier was originally single-
  domain with eval-loop reclassification in early ``2026-03..04``
  ``rag_pkg`` extraction; updated to multiple domains in ``2026-08-21``
  filter-fields work (``7bb3c2e``/``7c892c5``) alongside
  ``RetrievalQueryOutput`` simplification.
