---
status: "proposed"
date: TBD
decision-makers: klea-scientific maintainers
consulted: none recorded
informed: none recorded
---

# Scientific mode: retrieval-side evidence decision and claim verification

## Context and Problem Statement

Scientific mode requires that every evidence-requiring plan step be grounded
before execution and that every claim behind the answer be verified before the
result is returned. Turning that requirement into an implementable contract
leaves two questions that the general path does not answer:

1. Where is it decided that a step requires evidence -- in the plan, as a
   field the planner sets, or in the retrieval node, as a consequence of how
   the graph is routed?
2. Where do per-claim verdicts live, and which component is allowed to write
   them, given that the operational evaluator already writes step statuses and
   the retrieval node already writes evidence?

The answer also has to name the state shape that carries claim verdicts, its
serialization entries, and what happens when a lookup finds nothing. The
design reasoning and the code evidence inventory are recorded in
`devdocs/system/scientific-mode.md` sections 9.1-9.3; this record states only
the decision of record.

Relevant background and constraints: ADR-0029 invariants 1 and 11 make control
flow workflow-owned so the model "cannot decide to omit required grounding";
ADR-0035 introduces the additive correctness layer; ADR-0036 scopes grounding
enforcement to scientific mode; ADR-0041 settles plan-step granularity;
ADR-0044 requires strict structured output; ADR-0051 maps review steps onto
interrupt/resume.

Code evidence relied on: `graph/base.py:630-644` (node registration and edge
wiring), `stores.py:230-254` and `stores.py:441-455` (stores-retrieval
implementations shared in `klea_utils`, no RAG instance or orchestrator),
`classify_question.py:129-149` (catalogue-constrained classification pattern),
`schemas.py:314-326` and `schemas.py:842-878` (state and result schemas),
`klea_agent.py:151-179` (graph construction) and
`operational_evaluator.py:328-367` (existing evaluator state writer).

## Decision Drivers

* Grounding must not be optional. A step the model chooses to skip must still
  be grounded if it needs evidence; ADR-0029 invariants 1 and 11 forbid
  leaving that choice to the model.
* The compiled graph already keys its traversal on `state.mode.resolved`, so
  scientific mode can insert nodes without forking the graph.
* A plan-side field is a schema change under ADR-0044 strict structured
  output and a decision the planner can get wrong; the granularity settled by
  ADR-0041 does not include an evidence marker.
* One writer per state key keeps state invariants checkable by inspection and
  by test.
* The stores-retrieval implementations already shared in `klea_utils` are
  sufficient; no new retrieval service is needed.
* Failure must be explicit: an unsuccessful lookup ends in `unverified`, not
  in a silent pass.

## Considered Options

1. **Plan-side marker.** The planner sets `StepSchema.requires_claim` and the
   retrieval node reads it.
2. **Routing guarantee.** Every executable step is routed through
   `Retrieving evidence`, which decides necessity, catalogue domain and query
   together; no plan-side marker exists.
3. **Verifier as an operational-evaluator branch.** The epistemic check runs
   as a mode-conditional branch inside the existing evaluator instead of as
   its own node.

## Decision Outcome

Chosen option: option 2, the routing guarantee, combined with a separate
`Verifying claims` node.

**Node composition and order.** Scientific mode runs `Retrieving evidence`
between `Planning` and the execution-loop entry -- `Binding tool calls` for
`tool` steps and `Reasoning` for `reasoning` steps -- for the executable steps
it is routed. After execution the graph runs `Verifying claims` and then
`Evaluating`. General mode keeps the direct `Resetting state` -> `Planning`
edge and gains no node.

**Per-step evidence decision.** For each executable step it is routed,
`Retrieving evidence` decides, in one place: whether evidence is required for
that step, which domain the lookup is confined to under the catalogue, and the
query itself. Because the trigger is the routing, the step-evidence decision
cannot be omitted by the planner. No plan-side marker is added;
`StepSchema.requires_claim` is not introduced.

**Claim records and state key.** Per-claim verdicts are carried by a
`ClaimVerification` record (claim identity, verdict, the sources relied on)
and a `ClaimSource` record (the evidence reference a claim is checked
against). They are stored under the state key
`claim_verification: dict[int, list[ClaimVerification]]`, keyed by step index,
and both models are added to the msgpack registration so a scientific run
serializes and resumes without losing record types.

**Bounded re-query loop.** An unsuccessful lookup triggers at most 2
re-retrievals per step. On reaching the cap the step ends `unverified` with a
record present, rather than being retried indefinitely or treated as grounded.

**Single verdict writer.** `Verifying claims` is the sole writer of
`claim_verification`; its `_update_state` is the only code that sets a verdict.
Its two duties are domain correctness (the source belongs to the approved
catalogue domain for the step) and claim validity (the source actually
supports the claim). `Retrieving evidence` writes evidence, the chosen domain
and the query only. `OperationalEvaluator._update_state` keeps writing step
statuses and attempt counts only. `EvaluationSchema` gains no groundedness,
coverage or confidence field: per-claim granularity lives in the records, so
assurance stays two-valued -- `verified` only when every claim of every
evidence-requiring step verifies, otherwise `unverified`.

### Consequences

* Positive: grounding cannot be bypassed by the planner; the evidence
  decision, the retry policy and the verdict writer each live in exactly one
  place; the general path is untouched; the existing shared stores-retrieval
  implementations are reused rather than duplicated.
* Positive: assurance semantics stay small and auditable, while per-claim
  detail is not lost.
* Negative: `Retrieving evidence` must classify evidence necessity itself, an
  extra model call per routed step, following the constrained-classification
  pattern at `classify_question.py:129-149`.
* Negative: the plan no longer states which steps will be grounded, so plan
  review (ADR-0051) cannot show it ahead of execution.
* Neutral: the mechanism ratifies nothing. ADR-0029, ADR-0030, ADR-0035 and
  ADR-0036 keep their current status, and the ratify-or-rewrite action on
  ADR-0036 remains open.

### Confirmation

* The compiled scientific graph contains exactly one `Retrieving evidence`
  node and one `Verifying claims` node, and a mode-keyed traversal test visits
  both in the order `Planning` -> `Retrieving evidence` -> execution entry ->
  `Verifying claims` -> `Evaluating`; general-mode traversal visits neither.
* A search of the codebase finds no writer of the `claim_verification` state
  key other than `Verifying claims._update_state`, and no writer of a verdict
  field in `Retrieving evidence` or in `OperationalEvaluator._update_state`.
* `StepSchema` has no `requires_claim` field, and the planner schema diff
  introduced by the change set contains no evidence marker.
* A step whose lookup fails twice terminates with assurance `unverified` and a
  `ClaimVerification` record present for the step, not with a missing record.
* A msgpack round-trip of a state containing `claim_verification` restores
  `ClaimVerification` and `ClaimSource` instances rather than plain dicts.
* `EvaluationSchema` is unchanged: no groundedness, coverage or confidence
  field was added.

## Pros and Cons of the Options

### Plan-side marker

* Pro: the plan states explicitly which steps will be grounded, so review sees
  it before execution.
* Pro: no change to the retrieval node's own logic.
* Con: the planner can omit or mis-set it, which makes grounding optional in
  practice and contradicts ADR-0029 invariants 1 and 11.
* Con: it changes `StepSchema` under ADR-0044 strict structured output for a
  decision the retrieval node has to make anyway.
* Con: it splits one decision (does this step need evidence, from which
  domain) across two components.

### Routing guarantee

* Pro: the trigger is structural -- every executable step passes the node, so
  grounding cannot be skipped.
* Pro: necessity, catalogue domain and query are decided together, in one
  component, with no schema change.
* Con: evidence necessity must be classified at retrieval time, costing an
  extra constrained call per routed step.
* Con: the plan under-reports which steps will be grounded, weakening
  pre-execution review.

### Verifier as an operational-evaluator branch

* Pro: no additional graph node.
* Con: one writer would carry two contracts (operational judging and
  epistemic verification), so the sole-writer invariant becomes conditional
  and much harder to test.
* Con: it conflicts with ADR-0035 and ADR-0036, which describe the correctness
  layer as additive and separately identified.

## More Information

* Design reasoning, code evidence inventory and file:line citations:
  `devdocs/system/scientific-mode.md` sections 9.1-9.3, and the settled rows
  1, 2, 3 and 5 of its section 5 table.
* Related records: ADR-0029 (invariants 1, 5 and 11), ADR-0035, ADR-0036,
  ADR-0041, ADR-0044, ADR-0051.
* Still open and deliberately not decided here: provenance data model and
  no-bypass enforcement (scientific-mode.md section 5 row 4); claim and
  citation authorship and the citation schema (rows 6 and 9); approved or
  curated source definition (row 6); mode classification and resolution
  (row 7); one-way downgrade enforcement (row 8); plan-approval mapping
  (row 10); the evaluation harness (row 12); frontend scientific UX (row 13);
  documentation hygiene (row 14); and the ratify-or-rewrite action on
  ADR-0036 (row 11).
* Status note: this record proposes a decision of record and ratifies nothing.
  It does not move ADR-0029, ADR-0030, ADR-0035 or ADR-0036, and the date
  field is to be recorded when the decision is ratified.
