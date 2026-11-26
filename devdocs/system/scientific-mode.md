# Scientific mode: design status, ADR map and open questions

Status: **draft note (proposal)**.  Scientific mode is not designed as a
whole.  Section 9 settles the single-graph topology, the retrieval attachment
points and the step-evidence marking rule, the claim record shape, the
bounded re-query loop and the verdict writer; what actually remains open is
evidence-to-execution provenance with no-bypass enforcement and claim and
citation authorship.  This note records where the design actually stands after
the general (operational) path landed, so the scientific layer is not mistaken
for an implemented or ratified design.  It is internal material: do not link
it from `docs/`, and update the public documentation and `CHANGELOG.md` only
when the design is implemented (see `devdocs/README.md` and `AGENTS.md` for
the ASCII-only and one-file-per-topic conventions).

## 1. Purpose and scope

Scientific mode is the correctness-centred operating mode of ADR-0030: the
general base plus the ADR-0029 correctness layer (grounding, evidence
inspection, plan gating, independent verification, provenance).  Only the
general base exists today; the correctness layer has no graph nodes, no state
fields and no ratified decision.  A scientific-mode design therefore spans:

- the agent graph: node set, edges, and the state fields the correctness
  layer needs;
- the API: route decisions, SSE/stream events, and the runtime context
  payloads the frontends consume;
- the frontends (web and TUI): mode, assurance, evidence, citations, and the
  cannot-ground ask;
- retrieval: the compulsory retrieval surface (the stores-retrieval implementations shared in `klea_utils`; no RAG instance or orchestrator is contacted) and how a domain is chosen;
- evaluation: the epistemic verifier and the harness that can validate a
  `verified` claim;
- documentation and ADR ratification.

The general path is unaffected by anything below: ADR-0035 states that the
general path contains no mandatory retrieval, evidence, gating, verification
or provenance stages.

## 2. ADR map (corrected status)

| ADR | Status (as recorded) | Scope |
|---|---|---|
| ADR-0029 scientific correctness architecture | accepted, 2026-09-02 | States invariants 1-11 as requirements (grounding, evidence distinctness, evidence inspection, explicit plan, gated consequential execution, attributable execution, explicit verification, closed provenance, invalidation-triggered replanning, replan progress, workflow-owned control flow).  No implementation confirmation claim: accepted-invariant plus unimplemented is not a contradiction. |
| ADR-0030 operating modes and assurance levels | accepted, 2026-09-02 | Defines Scientific vs General mode; assurance is structured state, not text.  Option A (strict refusal without a curated source) is retained as the behaviour *inside* Scientific mode; "no silent downgrade" is a driver. |
| ADR-0035 general operational agent path | proposed, 2026-09-19 | Governs the general (operational) path and explicitly excludes the epistemic stages from it.  Its line 14/43-44/51 boundary is normative only for the general path. |
| ADR-0036 grounding enforcement | proposed, 2026-09-10 | The boundary: General mode MAY ground (optional, operational evaluator, results labelled unverified); Scientific mode MUST ground (mandatory, epistemic evaluator, results labelled verified).  Distinction is enforcement, not capability. |

The three scientific-facing ADRs (0029, 0030, 0036) are not a ratified design:
0029 and 0030 are accepted as requirements and boundaries, 0036 is still
proposed, and no ADR describes a scientific graph.

## 3. Premise corrections recorded from the evidence

Four frequently repeated statements conflict with the files and are corrected
here:

1. ADR-0029 and ADR-0030 are `accepted`, not proposed.  Only ADR-0035 and
   ADR-0036 are `proposed`, so "all scientific ADRs stay proposed" is not the
   current record.  What is missing is an implementation/design ADR, not the
   acceptance of the two foundational ones.
2. Mode is not delivered through `client_context`.  The single authority is
   the checkpointed `Mode` in graph state, mutated by the `/mode` graph
   command (the UI selectors dispatch that same command).  `client_context` is
   a separate, loopback-only runtime carrier (cwd/env/interpreter) that is
   explicitly never placed in graph state.
3. The assurance literal implemented in state is `unverified`, not
   `unassured`; it defaults to `unverified` and nothing sets `verified`.
4. The files assert the additive relationship explicitly (ADR-0035 near line
   187 and ADR-0036 sections on the boundary: "the scientific path is the
   general base plus an additive correctness layer").  The single-graph
   topology is the settled reading (section 5, row 1, and the retrieval
   attachment points in sections 9.1 and 9.2), not a still-open question.

## 4. Disposition of the twelve earlier findings

Each earlier finding is re-read against the ADR and system-note evidence and
the corrections in section 3, and labelled dissolved (no real conflict),
amended (a genuine open decision rather than a contradiction) or retained (a
concrete actionable defect).

Dissolved:

- (2) "The epistemic evaluator contradicts the operational one."  ADR-0036
  names an operational and an epistemic evaluator; only the operational one
  exists (`OperationalEvaluator`).  The epistemic evaluator is undesigned and
  ADR-0035 excludes it from the general path, so there is no conflict.
- (3) "Refusing without a source contradicts no-silent-downgrade."  ADR-0030
  option A (strict refusal, retained inside Scientific mode), the driver
  "no silent downgrade" and ADR-0036's "MUST ground" agree: stop and ask,
  never downgrade.  Only the ask itself is open (`ModeDecision`,
  `ModeInformer` and `Mode.note` already exist as a skeleton).
- (5) "Assurance is never set to verified."  The field exists
  (`Mode.assurance`, default `unverified`); the deferral is explicit and
  correct while no verification exists.
- (6) "The C4 diagram shows a scientific layer that does not exist."  The
  layer is draft/proposed, so the depiction is aspirational; a caption or
  pointer is documentation hygiene, not a design conflict.
- (9) "System docs cite proposed ADRs normatively."  A wording issue: 0035 and
  0036 are cited as normative while proposed; 0029 and 0030 are accepted.
  The fix is to state each ADR's status at the citation.
- (10) "RAG and scientific mode are the same thing."  RAG is the concrete
  retrieval implementation; scientific mode must retrieve and must decide
  how.  The "RAG/scientific mode" phrasing is the defect; the hybrid (MCP
  tool for general, in-process compulsory node for scientific, one shared
  resolver) is drafted but unratified.
- (11) "Accepted correctness invariants without implementation."  ADR-0029
  states invariants as requirements with no confirmation claim; only the
  operational evaluator exists and assurance stays `unverified`.

Amended (a real open decision, not a contradiction):

- (1) "One evaluator node or two?"  Not settled either way.  Residual code
  inconsistency to align: `operational_evaluator.py` promises both "the same
  judge contract" and "a separate, independent, epistemic verifier", while
  `answer_from_results.py` says the evaluator contract "stays the same".
- (7) "Two competing sources of truth for mode."  Corrected: complementary,
  not competing.  One authority is the checkpointed `Mode` in graph state,
  mutated by the `/mode` graph command; `client_context` is a separate
  runtime carrier for cwd/env/interpreter.  The carrier of a mode change is
  the command, not `client_context`.
- (12) "Scientific mode duplicates or contradicts the general path."  Corrected,
  and now decided: there is one graph, not two.  A single compiled
  `StateGraph` is traversed according to `state.mode.resolved`; the
  scientific-only stages are inserted as additional nodes on that same graph
  and are reached only through mode-gated routers.  No second compiled graph
  and no parallel scientific graph will be built.  ADR-0035 governs the
  general path and ADR-0029 the correctness layer that ADR-0030 scopes to
  scientific mode.

Node names in this note are the registered graph labels used in agent_pkg/klea_agent/klea_agent.py (Resetting state, Planning, Evaluating, Binding tool calls, Reasoning); the implementing class is named once in parentheses where it aids reading (Evaluating (OperationalEvaluator)).  Retrieving evidence and Verifying claims are new labels to be registered under those exact names.

Retained (actionable defects):

- (4) One-way downgrade is a rule without enforcement.  `/mode [general|
  scientific]` permits both directions and the UI selectors dispatch the same
  command; `Mode` carries requested/resolved/assurance/note but no downgrade
  marker, so a general-to-scientific upgrade is neither rejected nor
  recorded.
- (8) The stores domain-search note is stale: its status line still reads
  "draft reference note ... locked but not yet implemented tool contract"
  although the tool ships.  It also says "RAG/scientific mode", which is
  misleading: scientific mode uses the stores-retrieval implementations
  shared in `klea_utils` (the same retrieval `klea_rag` wraps) and does not
  interact with a RAG instance.  The compulsory-node versus optional-tool
  distinction is already documented there; only the status line still needs
  fixing.

## 5. Ranked open design questions

Ranked by design dependency.  "Blocking" means no scientific-mode
implementation can be decided before it; "layers" names what each answer
changes.

| # | Question | Blocking | Layers |
|---|---|---|---|
| 1 | Scientific graph topology: decided -- one compiled graph whose traversal is keyed on `state.mode.resolved`; scientific mode inserts `Retrieving evidence` on the edge from `Resetting state` to `Planning`, while general mode keeps the direct `Resetting state` -> `Planning` edge (klea_agent.py:926). | no -- decided | backend graph/state, API events, web and TUI labels/panes, docs |
| 2 | Epistemic verifier: a separate `Verifying claims` node running after execution and before `Evaluating`, with its verdict contract and claim record shape decided (section 9.3).  `EvaluationSchema` gains no groundedness/coverage/confidence field; per-claim granularity is carried by the `claim_verification` records. | no -- decided | backend node/schemas/router, API verdict events, web and TUI rendering, docs |
| 3 | Compulsory retrieval node and the stores-retrieval implementations shared in `klea_utils` (no RAG instance or orchestrator is contacted): placement decided -- the mid-plan `Retrieving evidence` sits between `Planning` and the execution-loop entry (`Binding tool calls` for `tool` steps, `Reasoning` for `reasoning` steps) for the executable steps it is routed -- so the step-evidence trigger is the routing guarantee: every executable step passes through `Retrieving evidence`, which decides necessity, catalogue domain and query together (sections 9.1 and 9.2).  Also decided: the step-evidence marking rule, the per-step domain choice and the evidence state shape (section 9.1), and a bounded re-query loop capped at 2 re-retrievals per step for an unsuccessful lookup (section 9.3). | no -- decided | retrieval layer, backend, API, web inspector, docs |
| 4 | Provenance data model and no-bypass enforcement: evidence to step to execution to verification, with a scientific run unable to reach the answer without grounding and verification. | yes | backend state/schemas, API inspect/context, web and TUI provenance view, evaluation, docs |
| 5 | Assurance semantics: decided -- only the planned `Verifying claims` node writes `claim_verification` verdicts (its `_update_state` is the sole writer of that state key); `Retrieving evidence` writes evidence only and `OperationalEvaluator._update_state` writes step statuses and attempt counts only.  `verified` is set only when every claim of every evidence-requiring step verifies, else assurance stays `unverified`; the two values suffice because per-claim granularity is carried by the records (section 9.3). | no -- decided | backend state, API context/route payload, web and TUI label, docs |
| 6 | Approved/curated-source definition and the cannot-ground ask: criteria, curation owner, and the options offered (supply a source / explicitly switch to general / cancel). | yes | docs, backend `ModeDecision`/`ModeInformer`, API ask payload, web and TUI ask UI |
| 7 | Mode classification and resolution: source presence versus explicit request versus user choice, and the `ModeInformer` text. | yes | backend, API, web and TUI, docs (skeleton exists) |
| 8 | One-way downgrade enforcement: reject general-to-scientific silently changing the mode, record the downgrade, and disable the upgrade in the selectors. | yes | backend command/state, API, web and TUI selectors, docs |
| 9 | Citation-carrying answer variant replacing or extending `AnswerFromResults`: citation schema, what a citation references, and rendering. | yes | backend node/schema, API SSE payload, web and TUI renderer, docs |
| 10 | Scientific plan-approval / HITL mapping (ADR-0029 invariant 5) onto review steps (ADR-0051) and interrupt/resume (ADR-0046). | yes | backend, API pending interrupt, web and TUI review form, docs |
| 11 | ADR ratification: a scientific-mode ADR once topology and verifier are decided; ratify or rewrite ADR-0036; decide whether ADR-0029/0030 stay accepted. | not blocking design; required before implementation is a decision of record | docs |
| 12 | Scientific evaluation harness: can any `verified` claim be validated against a task set. | not blocking build; blocking validation of a `verified` claim | evaluation, docs |
| 13 | Frontend scientific UX: mode, assurance, evidence, citations, the ask, and TUI parity. | not blocking (follows the API shapes) | web, TUI, API |
| 14 | Documentation hygiene (section 6). | not blocking | docs |

## 6. Documentation hygiene actions

1. `devdocs/system/stores-domain-search-tool.md`: change the status from
   draft to accepted, add the one-line restatement of tool call (optional,
   LLM-chosen) versus retrieval node (compulsory, once the topology is
   settled), and fix the "RAG/scientific mode" phrasing.
2. Align the two evaluator docstrings: `operational_evaluator.py` ("the same
   judge contract" versus "a separate, independent, epistemic verifier") and
   `answer_from_results.py` ("the evaluator contract stays the same").
3. Add this note to the `devdocs/README.md` index and caption the C4 agent
   component and the general-path control flow with "scientific layer not
   designed" (follow-up, outside this change set).
4. Reword normative citations of the still-proposed ADR-0035 and ADR-0036 so
   each citation carries its status.
5. Settle the ADR-0029/0030 status against a "proposed until designed" rule,
   or state explicitly that accepted requirements plus an unimplemented layer
   is the intended record.

## 7. Next steps

Nothing in this note is ratified.  The sequence that follows from it is:
ratify the decisions recorded for questions 1, 2, 3 and 5, settle question 4
and questions 6-10 (source definition, mode resolution, downgrade enforcement,
the answer variant and the approval mapping), then record the outcome as a
new ADR and ratify or rewrite ADR-0036.  Until then, the decisions above remain unratified and unimplemented, and the general path remains the only implemented path.

## 8. Decision tracking (proposed answers, not ratified)

Added: scientific-mode design-tracking pass (edit session; date not recorded,
fill it in at ratification).  Each entry records the answer proposed for the
question of section 5, the evidence it rests on, and its status.  `proposed`
means an answer is drafted and still needs ratification; `decided` is reserved
for a ratified ADR; `blocked` marks a question no current file can settle.
This section records proposals only.  It changes no ADR status: ADR-0029 and
ADR-0030 stay `accepted`, ADR-0035 and ADR-0036 stay `proposed`.

1. Scientific graph topology -- additive layer: the general base plus the
   ADR-0029 correctness layer, reusing the general graph.
   Rationale: ADR-0035 states "the scientific path is the general base plus an
   additive correctness layer" (ADR-0035 near line 187, restated at
   scientific-mode.md:63-67); ADR-0030 scopes the correctness layer to
   scientific mode (scientific-mode.md:13-14).
   Status: decided as the topology choice (section 9.2); ratification pending.
   Sections 9.1 and 9.2 fix the node set, edges and state fields; the
   correctness layer has no graph nodes and no state fields implemented today
   (scientific-mode.md:16-17).

2. Epistemic verifier -- a separate, independent node rather than a
   mode-conditional branch of the operational evaluator.
   Rationale: ADR-0029 invariant 7 requires verification independent of
   generation (adr/0029:75); the operational evaluator "is not an epistemic
   verifier and must not be presented as one"
   (agent-topology-literature-review.md:180); ADR-0036 makes the scientific
   evaluator epistemic (adr/0036:60-62).  One node is technically possible
   through the per-run dynamic output schema (scientific-mode.md:145), and
   the settled choice is the separate node, which keeps the independence
   claim intact.
   Status: settled as node shape (section 9.3, "Claim record shape" and
   "Verdict writer and assurance conditions"): the separate `Verifying
   claims` node runs before `Evaluating`, and the verdict contract needs no
   groundedness/coverage/confidence field because per-claim granularity is
   carried by the `claim_verification` records.

3. Compulsory retrieval node and how scientific mode uses the shared klea_utils
   stores retrieval -- a compulsory
   node on an unconditional edge that always runs and writes retrieved
   evidence into checkpointed state; the domain comes from the configured
   catalogue.
   Rationale: ADR-0029 invariant 1 (steps "retrieve or receive the evidence
   required" before execution, adr/0029:66) and invariant 3 (evidence
   inspectable before planning and before execution, adr/0029:68); the
   compulsory-node versus optional-tool distinction is already recorded
   (stores-domain-search-tool.md:4-6, :553-554, :562-564).
   Status: settled as the placement rule (sections 9.1 and 9.2): the mid-plan
   `Retrieving evidence` runs for the executable steps it is routed, the
   domain comes from the configured catalogue, and the step-evidence marking
   rule and the evidence state shape are settled; an unsuccessful lookup is
   the bounded re-query loop of section 9.3.

4. Provenance data model and no-bypass enforcement -- an evidence to step to
   execution to verification chain held as graph state, with the answer
   unreachable unless each claim is grounded and verified.
   Rationale: ADR-0029 requires every claim traceable to its sources
   (adr/0029:21) and attributed to a plan step and its evidence (invariant 6,
   adr/0029:74); invariants 1 and 11 make control flow workflow-owned so the
   model "cannot decide to omit required grounding" (adr/0029:66, :79);
   ADR-0030 forbids silent downgrade (scientific-mode.md:39).
   Status: decided as the chain shape (section 9.3); ratification pending.
   Section 9.3 fixes the chain order and the `claim_verification` state key; the
   concrete data-model and schema shapes remain implementation work
   (scientific-mode.md:147).

5. Assurance semantics -- keep the two values; only a passing epistemic
   verification may set `verified`, everything else stays `unverified`.
   Rationale: ADR-0030 makes assurance structured state
   (scientific-mode.md:39); ADR-0036 reserves `verified` for the epistemic
   evaluator's passing results (adr/0036:60-62); the implemented literal is
   `unverified` and nothing sets `verified` (scientific-mode.md:61-62).
   Status: decided as the rule (section 9.3, "Verdict writer and assurance
   conditions").  Only the planned `Verifying claims` node writes
   `claim_verification` verdicts (its `_update_state` is the sole writer of
   that state key); `Retrieving evidence` writes evidence only; and
   `OperationalEvaluator._update_state` writes step statuses and attempt
   counts only (operational_evaluator.py:285, :360-368), gaining no claim
   writes.  Ordering: pre-planning retrieval -> `Planning` -> mid-plan
   retrieval -> execution -> `Verifying claims` -> `Evaluating`; `Evaluating`
   renders the records read-only as one optional prompt block and never
   rewrites or authors a verdict.  `verified` is set only when every claim of
   every evidence-requiring step verifies, else assurance stays `unverified`;
   the two values suffice because per-claim granularity is carried by the
   records, so `EvaluationSchema` gains neither `claim_coverage` nor
   `claim_reason`.

6. Curated-source definition and the cannot-ground ask -- on cannot-ground,
   stop and ask with exactly three options: supply a source, explicitly
   switch to general, cancel.  Never downgrade silently.
   Rationale: ADR-0030 option A, retained inside Scientific mode, plus the
   "no silent downgrade" driver (scientific-mode.md:39, :82-86); the three
   options are enumerated at scientific-mode.md:149.
   Status: proposed as the behaviour; blocked on the approval criteria and
   the curation owner.

7. Mode classification and resolution -- the mode is set at session start and
   driven by explicit request or user choice; source presence informs but does
   not alone decide.
   Rationale: ADR-0030 defines the two modes (scientific-mode.md:39); the
   `ModeDecision`/`ModeInformer`/`Mode.note` skeleton already exists
   (scientific-mode.md:85-86, :150).
   Status: proposed as the resolution rule; blocked on whether source
   presence may auto-select the mode, which interacts with question 8.

8. One-way downgrade enforcement -- reject general-to-scientific changes,
   record the downgrade with a `Mode` marker, and disable the upgrade in the
   selectors.
   Rationale: the retained defect that `/mode` permits both directions and
   `Mode` has no downgrade marker (scientific-mode.md:124-128), read against
   the "no silent downgrade" driver (scientific-mode.md:39).
   Status: proposed as the rule; the mechanism shape is still open.

9. Citation-carrying answer -- extend `AnswerFromResults` with citation data
   instead of authoring a new answer node, keeping citations
   mode-conditional.
   Rationale: ADR-0029 requires traceability to sources (adr/0029:21) and
   attribution to step and evidence (adr/0029:74); the open framing is
   "replacing or extending `AnswerFromResults`" (scientific-mode.md:152).
   Status: proposed as the variant choice; blocked on the citation schema,
   what a citation references, and the rendering.

10. Scientific plan-approval / HITL -- map ADR-0029 invariant 5 (gated
    consequential execution) onto review-as-plan-step (ADR-0051) and
    interrupt/resume (ADR-0046).
    Rationale: ADR-0029 invariant 5; adr/0051:2 and adr/0046:2 are both
    `accepted`, so no new mechanism is needed for the mapping.
    Status: proposed as the mapping; blocked on any scientific-specific
    additions beyond it.

11. ADR ratification -- keep ADR-0029/ADR-0030 `accepted` as requirements,
    write a new scientific-mode design ADR once topology (1) and verifier (2)
    are decided, and ratify or rewrite ADR-0036 last.
    Rationale: adr/0029:2 and adr/0030:2 are `accepted`, adr/0036:2 is
    `proposed`; the sequence follows scientific-mode.md:179-184 and :173-175.
    Status: proposed; not blocking design, required before implementation is
    a decision of record.

12. Scientific evaluation harness -- status: settled as scoped (section 9.3),
    and the open part is the harness task set itself.  The `claim_verification`
    record shape and the verdict writer it depends on are settled, so whether a
    `verified` claim can be validated against a task set is now a harness
    question (scientific-mode.md:162).

13. Frontend scientific UX -- status: blocked; deferred.  It "follows the
    API shapes" (scientific-mode.md:156), which depend on 1, 2 and 4.

14. Documentation hygiene -- status: proposed, and executable now: complete
    the five actions listed in section 6 (stores-domain-search-tool.md status
    and phrasing, evaluator docstring alignment, note indexing and C4
    caption, status at proposed-ADR citations, the ADR-0029/0030 status
    rule).
    Rationale: the concrete list at scientific-mode.md:159-175.

Remaining open questions: only two remain, both deferred to
`devdocs/backlog.md`: claim and citation authorship (9) and
evidence-to-execution provenance with no-bypass enforcement (4).  The
single-graph topology (1), the retrieval attachment points and the
step-evidence marking rule (3, section 9.1), the claim record shape and its
`get_allowed_msgpack_modules` registration (2, "Claim record shape"), the
bounded re-query loop for a repeat or unsuccessful lookup ("Bounded re-query
loop for an unverified claim") and the verdict writer and assurance
conditions (5, "Verdict writer and assurance conditions") are settled in
section 9, not open.
These two still need the ADR of question 11 before implementation.

## 9. Settled agreements from the design passes (not ratified)

Added: scientific-mode design passes over the planner contract, the
pre-planning retrieval query and the evaluator topology.  Each item records an
agreement reached in those passes, the evidence it rests on, and what stays
blocked.  This section ratifies nothing: ADR-0029 and ADR-0030 stay `accepted`,
ADR-0035 and ADR-0036 stay `proposed`, scientific mode stays undesigned as a
whole, and no node, state field or prompt text described here is implemented.
The settled mechanism and its consequences are written up as ADR-0056 (`devdocs/adr/0056-scientific-evidence-and-claim-verification.md`) and registered in `devdocs/README.md`.

The scientific topology is still the already-agreed two new registered labels,
`Retrieving evidence` and `Verifying claims`; the Planner adds no label and the
general path keeps every node it has today.

### 9.1 Claim marking rule and its checker

- The marking rule is the retrieval-side decision that routing guarantees, not a
  field the Planner authors.  In scientific mode every executable step -- one
  that is pending, carries no `needs_input` questions and is not a `review`
  checkpoint -- routes through `Retrieving evidence`, which decides in one node
  whether the step needs evidence, which catalogue domain applies and the
  retrieval query itself.  The node writes evidence only; it sets no verdict.
- The decision cannot be omitted.  The node's domain output is constrained to a
  runtime `list[Literal[tuple(all_domains)]]`
  (`utils_pkg/klea_utils/graph/base.py:630-644`), and a post-filter cannot yield
  an unlisted name (`rag_pkg/klea_rag/nodes/classify_question.py:129-149`), so
  an omission is inexpressible: the only exits are a catalogue domain or an
  explicit no-domain token.  A step that requires evidence is the step whose
  retrieval returned a catalogue domain.
- `ClaimSource.domain` must equal the domain chosen for the step.  The record's
  source locator and the retrieval decision therefore agree by construction, so
  a record naming a different domain is a defect rather than a variant.
- The constraint carries no `Field(description=...)`, because an LLM output
  schema is structure-only (`devdocs/system/prompt-conventions.md:104-136`,
  Rule 6, "LLM output schemas are structure-only").
- There is **one** `Planning` class and no second, scientific-only planner node:
  the class, its
  `model_role = "plan"`, `PlannerOutput`/`PlannerPlanSchema`/`PlanSchema`
  (including `validate_plan()`), `_validate_result` with
  `max_validation_retries`, the replan budget and its helpers, tool disclosure
  and the prompt-variable assembly are reused unchanged and must not be forked
  (planner.py:38-68, 105-143, 145-226, 229-286, 288-336, 338-469, 471-569,
  665-700).  A subclass would duplicate the budget and validation logic and let
  the two planners drift.  The `Planning` node keeps its existing label and its single
  graph instance: no second `Planning` node and no mode fork.  The single mode-gated
  pre-`Planning` edge adds `Retrieving evidence` before `Planning` in scientific
  mode.
- The prompt teaches no per-step evidence field.  The existing `Planner_system.md`
  gains one mode-gated section (a `## Scientific mode: claims and evidence` block
  rendered only when `state.mode.resolved == "scientific"`, through the
  existing optional-section pattern at planner.py:249-270) and a new pre-planning
  evidence input under `## Inputs you will receive`.  The per-step semantics live
  in the retrieval node's own prompt, because the marking decision belongs to
  that node.  There is no second prompt file, and general mode's prompt must not
  mention claims.
- The Operational Evaluator is unchanged and gains no flag or claim check: it
  keeps writing step statuses and attempt counts, never a verdict, and assurance
  stays the literal `unverified`.  No evaluator-side check sets `verified`.
- Generic plan validation is unchanged and stays fail-closed: a violation
  returns the error string, `execute` re-invokes with it as
  `validation_feedback` for up to `max_validation_retries = 3`
  (planner.py:68), and if it is still invalid the run fails closed:
  `_update_state` writes `_preserved_plan(prior, "unplannable", ...)` with
  `failure_reason = "plan validation failed: ..."` (planner.py:646-663), and
  assurance stays the literal `unverified`.  No silent repair, no silent
  downgrade.
- Label counts: the marking rule adds 0 registered labels and 0 serializer
  entries.  The two scientific node labels, `Retrieving evidence` and
  `Verifying claims`,
  add 2 graph labels, registered with `add_node(label, execute)` (precedent
  `klea_agent.py:721`/`:810`, `:757`/`:818`), for a running scientific-mode
  total of 2.  The serializer allow-list stays unchanged
  (`agent_pkg/klea_agent/klea_agent.py:151-179`;
  `utils_pkg/klea_utils/graph/base.py:874-878`, `:968-969`, `:978-979`).  A
  label is the status/stream key
  (`utils_pkg/klea_utils/ui/web/nicegui/components/status_pane.py:286-298`,
  `devdocs/system/streams.md:37`, `devdocs/system/tui.md:161`).

### 9.2 Pre-planning retrieval: corpus coverage

- The pre-planning instance of `Retrieving evidence` performs a query-free,
  O(domains) coverage check: no LLM call, no classifier node, no generated
  query and no per-document read.  It reports what the corpus is *about*
  rather than a guess at what to search for, and its cost is identical for a
  10-file store and a store of ~9000 files.  The guard/triage stage owns only
  chat-versus-task (`RouteSchema` has no query field, schemas.py:820-824), and
  the Planner cannot author the check because retrieval runs before it
  (`Resetting state -> Planner` is unconditional, klea_agent.py:926).
- What reaches the Planner, and only this:
  1. The user-authored per-domain description (`PerDomainConfig.description`,
     `utils_pkg/klea_utils/stores/config.py:75-76`), which is the human
     statement of what the domain is meant to contain.
  2. The existing catalogue descriptors from `render_catalogue(config)`
     (`utils_pkg/klea_utils/stores/stores.py:230-254`): `domain`,
     `description`, `vector_stores`, `bm25_stores` and `filter_fields` (name
     and value_type) per domain.  The catalogue already reaches the Planner
     indirectly through `search_stores.__doc__` (bundled_tools.py:358)
     rendered into `tools_description` (planner.py:123-143, :285); repeating
     it in the dedicated section is for visibility only.
  Both are O(domains), so the payload does not grow with the number of stored
  files.  Bounds, all deterministic: about 1,000 characters per domain
  description and about 4,000 characters for the whole section; every cap sets
  a visible `truncated` flag plus a note, mirroring the existing behaviour of
  `truncate_reference_material`
  (`utils_pkg/klea_utils/stores/stores.py:387-394`).
- No metadata-map read and no full store-summary load at runtime, because
  neither is O(domains):
  1. The ingestion manifest is per-file and lives in the source directory
     (`utils_pkg/klea_utils/stores/ingestion.py:743-746`, `:821-834`), so
     reading it is linear in the number of indexed files and would be a
     ~9000-entry read for a large store.
  2. `lint_store` needs the whole unpickled corpus and only prints
     (`utils_pkg/klea_utils/stores/postcheck.py:42-63`,
     `utils_pkg/klea_utils/ui/stores_create.py:625-630`), so it is not a cheap
     runtime probe.
  3. Per-document metadata (title, authors, year, venue, doi) lives only inside
     the BM25 documents reached by a query
     (`utils_pkg/klea_utils/stores/postcheck.py:164-171`,
     `utils_pkg/klea_utils/stores/retrieval/bm25.py:35-55`), so it is not
     available without a search and is not O(domains) either.
- Deferred: a creation-time store summary.  It would be cheap to produce from
  `lint_store`'s aggregate, but that aggregate reports how much is stored,
  never what, and any coverage-bearing variant reintroduces the linear
  artefact above.  If it is ever added it is optional, non-authoritative and
  degrades rather than fails: the run never depends on it.
- Fail-closed behaviour, the coverage check never being silently skipped: no
  configuration or an unreadable config returns the existing single synthetic
  error result with the catalogue retained (stores.py:206-227, :418-419), and
  `_catalogue_safe` degrades to `[]` on `StoresSearchError` (:452-455), so the
  node surfaces the error and the run continues to the cannot-ground path with
  assurance `unverified`.  The same closed state covers the per-step domain
  decision: with no catalogue, an unreadable catalogue or no domain that fits
  the step, the node runs no search, reports coverage as unknown, and the run
  ends in `unplannable` or a `needs_input` step, never in an `undefined` domain
  and never in an invented one.  Truncation is always visible as a flag plus a
  note.  The check has no `state.query` dependency, so an empty or non-task
  turn simply carries no task-specific coverage.
- How unknown coverage reaches the user.  The design does not pretend to know
  the contents of a store it did not read:
  1. Assurance stays the literal `unverified`; this node never sets `verified`.
  2. The cannot-ground path ends in `unplannable`, not in an answer, and
     `needs_input` steps ask the user when source existence is unknown
     (planner.py:381-397, :407-409).
  3. The answer states explicitly what could not be verified, backed by the
     per-step `claim_verification` verified/unverified counts and by the
     existing no-hit signal
     (`utils_pkg/klea_utils/stores/stores.py:357-365`) instead of an
     empty success.
- The payload is one run-scoped state field rendered as a new prompt section
  beside the existing `{discovery}` variable: the per-domain descriptions, the
  catalogue rows, the truncation flag and notes, and the explicit
  coverage-unknown signal.  It carries no per-domain top-k hits, no serialized
  reference material and no document rows.  The field sits inside the existing
  registered state classes, so `get_allowed_msgpack_modules` gains nothing
  (klea_agent.py:151-179).
- Cost, split by what runs where.  Catalogue rendering costs 0 LLM calls and
  0 added registered labels: it is a deterministic O(domains) read inside the
  already-agreed `Retrieving evidence` label, and no `search_stores` mode is
  added or changed.  The in-label per-step domain and query decision is one
  merged call per batch inside that same label, over the steps the Planner
  already hands out via `next_batch` (agent_pkg/klea_agent/schemas.py:314-326);
  it adds no label and no node.
- Option (i), reusing the RAG query generator, was rejected:
  `GenerateRetrievalQuery` is an LLM node (`model_role = "chat"`, temperature
  0.3, `memory=True`, generate_retrieval_query.py:40-43, :67), already
  registered as `Generating search` (rag.py:284, :288-291), so reuse costs one
  label plus one LLM call on every run before any planning; its inputs are
  `RAGState`-shaped (`state.query_domains` :83, `state.retrieval_attempts`
  :113, `state.text_response_eval.summary` :147,
  `state.retrieval_query.search_query` :148), so it needs adaptation or a fork;
  and its output is a single query string
  (`RetrievalQueryOutput.search_query`, klea_rag/schemas.py:53-56), a guess at
  what to search rather than an account of what the corpus holds.
- Option (ii), a bounded hybrid of the catalogue plus top-k hits for a derived
  short query, was rejected: it re-adopts the keyword query this design
  replaces, and with it the search failure modes that can fire before
  `Retrieving evidence` has decided the step needs evidence (empty-query
  rejection, stores.py:311-312; the no-hit note, :357-365; the embedding-model
  requirement, :319-326).  A catalogue-only coverage check needs neither an
  embedding model nor the cross-encoder (:319-326, :373-385), so it cannot
  fail for the reasons a search fails.  Targeted, phrase-quality queries stay
  the per-step work `Retrieving evidence` authors from the step's own
  `summary`/`details` (schemas.py:38-45), which is where a real query belongs.
  Only its `Literal[domains]`-constrained output pattern is adopted from
  `GenerateRetrievalQuery`, in-node: no LLM node and no separate label is
  reused.
- What this adds over the discovery refresh, which holds only the
  `AGENTS.md`/`CLAUDE.md` project text: the resolved domain catalogue, the
  user-authored statement of what each domain is meant to hold, its truncation
  state, and an explicit coverage-unknown signal.

### 9.3 Two evaluation nodes

- The agreed topology is two evaluation nodes in the scientific path:
  `Verifying claims` runs first, then the existing `Evaluating` (`OperationalEvaluator`).  The
  router and edge changes are keyed on `state.mode.resolved`, so the general
  path keeps its single evaluator and its current edges.
- Settled: the `claim_verification` verdict *contract* is covered end to end by
  "Claim record shape", "Bounded re-query loop for an unverified claim" and
  "Verdict writer and assurance conditions" below.  A verdict record carries
  the step number, the source locator, the
  verbatim quote, the verdict and the reason, with positional identity retained
  (no `claim_id` and no step-side claim list) as settled in "Claim record
  shape" below.  An unverified claim loops back to `Retrieving evidence` for a
  bounded number of extra passes and then stays `unverified`.  Only the planned
  `Verifying claims` node writes verdicts; `Retrieving evidence` writes the
  evidence, the chosen domain and the query, never a verdict.  Assurance stays
  the literal `unverified` until every claim of every evidence-requiring step
  verifies.

#### Claim record shape

The shape of the per-step claim record is settled, verdict contract included:
the loop rule is in "Bounded re-query loop for an unverified claim" and the
writer rule is in "Verdict writer and assurance conditions" below.  This
subsection supplies the concrete field shape, the container keying and the
`get_allowed_msgpack_modules` registration.

- **Class.** A new `ClaimVerification` model with a nested `ClaimSource`
  model, both plain `BaseModel`s in `agent_pkg/klea_agent/schemas.py`: every
  field is defaulted, so an older checkpoint still loads, and neither carries
  a `Field(description=...)`, matching `StepSchema` and `StepEvaluation`.
- **Fields.** `step_number: int = 0` (the related plan step, as requested);
  `claim: str = ""` (one atomic, self-contained assertion, so it can be
  checked against a single passage); `source: ClaimSource` (the source
  reference, as requested); `quote: str = ""` (the verbatim supporting
  excerpt, capped in length, empty when the claim is not verified);
  `verdict: Literal["", "verified", "unverified"] = ""` (empty means not yet
  checked, mirroring `Mode.assurance` and `StepEvaluation.verdict`);
  `reason: str = ""` (why the source does not support the claim, required
  when the verdict is not `verified`).
- **Source reference.** `ClaimSource` holds `domain: str = ""`,
  `file_name: str = ""` and `headings: list[str] = []` -- exactly the
  locators `search_stores` exposes on a hit.  `rank` and `score` are
  deliberately excluded: they are per-query and unstable, so they are not
  provenance.  Identity is positional: there is no `claim_id` field and no
  step-side claim list.  A claim's integer identity is its position in
  `claim_verification[step_number]`, and `ClaimVerification.step_number` is
  the back-reference, mirroring `step_outputs: dict[int, list[StepOutput]]`
  and `human_input: dict[int, list[str]]` (schemas.py:1164).  Append-only
  list order is stable, so no new state key and no further
  `get_allowed_msgpack_modules` entry are needed.  A stored id would dangle
  across the Planner's plan revision renumbering (planner.py:577-584), which
  is exactly why identity stays positional.  A citation is the record ->
  `source` pair plus `step_number`; `ClaimSource` carries only domain,
  file_name and headings because `render_catalogue` exposes no document ids
  (stores.py:230-254).
- **Container and keying.** One record per claim, not one per step: a step's
  output usually asserts several independent facts, each needing its own
  verdict, quote and source, and a single record per step could not express a
  mixed outcome.  Graph state carries
  `claim_verification: dict[int, list[ClaimVerification]]` on
  `KleaAgentState`, keyed by step number, in the same shape as
  `step_outputs: dict[int, list[StepOutput]]` and `human_input:
  dict[int, list[str]]`.  The state key stays `claim_verification`, as this
  note already names it.
- **Msgpack allowance.** `ClaimVerification` and `ClaimSource` are both added
  to the list returned by `KleaAgent.get_allowed_msgpack_modules`
  (klea_agent.py:151-179).  Nested types need their own entry, the same
  precedent as `PlanSchema`/`StepSchema` and
  `EvaluationSchema`/`StepEvaluation`.
- **Evaluator handoff.** The verifier's own output schema must be a flat list
  of records each carrying `step_number`, because a dynamic-key object is
  closed to `{}` by strict structured-output modes; code groups the list into
  the state dict, exactly as `EvaluationSchema.evaluations` is grouped by
  `step_number`.  `Evaluating` reads state, not the verifier's output: its
  prompt assembly already computes the batch from `plan.next_batch` plus the
  `depends_on` closure, and renders the records for those step numbers as one
  more optional prompt block, in the same way as the recent-tool-failures
  block.  `Verifying claims` runs before `Evaluating`, so the records are
  present when it reads them.
- **Composition with the per-step verdicts.** The composition rule is the
  bounded re-query loop in "Bounded re-query loop for an unverified claim",
  and the writer rule is "Verdict writer and assurance conditions" below,
  which settles the verdict writer, its conditions and whether two assurance
  values suffice: only the planned `Verifying claims` node writes
  `claim_verification` verdicts, `verified` is set only when every claim of
  every evidence-requiring step verifies, and the two values suffice because
  per-claim granularity is carried by the records.  `EvaluationSchema` gains
  neither `claim_coverage` nor `claim_reason`; the answer cites the per-step
  records plus the existing no-hit signal.  Claim and citation authorship is the only
  part of item 6 still open; claim identity is settled above.  How many
  records exist at all -- what decides that a step requires evidence -- is
  settled in sections 9.1 and 9.2.

#### Bounded re-query loop for an unverified claim

The bounded re-query loop is decided: an unverified claim does not end
execution and does not silently proceed either.  `Verifying claims` may send
the claim back to
`Retrieving evidence` for a bounded number of extra passes, and only then
does the claim stay `unverified` and the run continue to `Evaluating`.

- **Shape.** The loop re-runs only `Retrieving evidence` <-> `Verifying
  claims`, adding 0 registered labels beyond the two scientific labels
  already agreed in section 9.1.  `Evaluating` still runs after it, on the
  records the loop leaves behind, so a record's verdict is final by the time
  the evaluator reads it.
- **Counter and cap.** One deterministic per-step counter, shaped exactly
  like `step_attempt_counts`/`tool_retry_counts` (`klea_agent.py:362-363`),
  counts the extra retrieval passes.  The cap is 2 re-retrievals per step, so
  a step makes at most 3 retrieval passes in total: the first pass plus two
  re-queries.  The counter is state, not model output, and the verifier
  cannot extend its own budget.
- **Feedback channel.** The re-query is driven by the record's own
  `ClaimVerification.reason`, the field that already carries why a source
  does not support a claim (see the claim record shape above).  No new field
  is introduced: that reason text is the query-quality feedback the next
  retrieval pass reads, and it also carries the corrected target domain for
  that pass, so the retry re-queries the right catalogue domain instead of
  repeating the failed one, in the same way `replan_reason` feeds the Planner
  and the deterministic retry counters feed their node
  (`operational_evaluator.py:344-349`, `:360-367`).  Nothing else in the loop
  changes: the cap of 2 re-retrievals per step, the `Retrieving evidence` and
  `Verifying claims` labels and the per-step counter stay as stated below.
- **Terminal outcome.** When the cap is reached, or when a pass still cannot
  support the claim, the record keeps `verdict = "unverified"` with its
  `reason` set, the run proceeds to `Evaluating` and then to the answer, and
  assurance stays the literal `unverified`.  Nothing in this loop ever sets
  `verified`, and an exhausted loop is not a run failure: the answer states
  what could not be verified instead of answering as if it had been.
- **Why this path, not an intra-node retry.** The retry is modelled on the
  cross-node feedback path, not on the intra-node `max_validation_retries`
  loop (`utils_pkg/klea_utils/nodes/abstract.py:354-360`, `:435-440`,
  `:477-497`).  That intra-node loop re-invokes a single LLM call with an
  error string and cannot re-run retrieval, which is another node, and it is
  already the authoring-validation budget, so reusing it here would merge two
  different failure classes into one counter.
- **Rejected alternatives.** An unbounded or repeating loop has no terminal
  state, so a claim the corpus cannot support would hang the run instead of
  producing an explicit `unverified` verdict.  Routing an unverified claim
  back to the Planner as `need_replan` rewrites the plan, not the evidence:
  it spends plan budget without giving the claim a better source, and it
  would let an epistemic outcome rewrite the plan the evaluator is meant to
  check.  An intra-node retry was rejected for the reason above.

#### Verdict writer and assurance conditions

The verdict writer and the assurance conditions are decided.  The bounded
re-query loop in "Bounded re-query loop for an unverified claim" decides what
happens to an unverified claim; this subsection decides who writes a verdict
and when the run-level assurance may become `verified`.

- **Single verdict writer.** Only the planned `Verifying claims` node writes
  `claim_verification` verdicts: its `_update_state` is the sole writer of that
  state key, and it has two duties -- it attests the claim's validity against
  the quote, and it attests that the domain recorded in `ClaimSource.domain` is
  the domain the step's retrieval actually chose.  `Retrieving evidence` writes
  only the evidence, the chosen domain and the query, never a verdict.
  `OperationalEvaluator._update_state` writes step statuses and attempt counts
  only (operational_evaluator.py:285, :360-368) and gains no claim writes.
- **Ordering.** Pre-planning retrieval -> `Planning` -> mid-plan retrieval ->
  execution -> `Verifying claims` -> `Evaluating`.  `Evaluating` renders the
  records read-only as one optional prompt block in the way it renders the
  recent-tool-failures block, and never rewrites or authors a verdict.
- **When `verified` is set.** Only when every claim of every evidence-requiring
  step verifies; otherwise assurance stays the literal `unverified`.
- **Two values suffice.** Per-claim granularity is carried by the records, so
  the run-level assurance needs only `verified`/`unverified`.
  `EvaluationSchema` gains neither `claim_coverage` nor `claim_reason`; the
  answer cites the per-step `claim_verification` verified/unverified counts plus
  the existing no-hit signal
  (`utils_pkg/klea_utils/stores/stores.py:357-365`).
- **Documentation hygiene.** The stale `operational_evaluator.py:65-66`
  docstring stays the existing documentation-hygiene item and is unchanged by
  this decision.
