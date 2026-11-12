# Scientific mode: design status, ADR map and open questions

Status: **draft note (proposal)**.  Scientific mode is not designed; every
item below is open.  This note records where the design actually stands after
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
- retrieval/RAG: the compulsory retrieval surface and how a domain is chosen;
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
   general base plus an additive correctness layer").  "Different topologies"
   is therefore a permitted reading of a still-open question, not the
   recorded decision.

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
- (12) "Scientific mode duplicates or contradicts the general path."  ADR-0035
  governs the general path, ADR-0029 the correctness layer that ADR-0030
  scopes to scientific mode.  But the docs assert "scientific = general base
  plus additive correctness layer", so additive versus separate topology is
  itself the open question, not a recorded decision.

Retained (actionable defects):

- (4) One-way downgrade is a rule without enforcement.  `/mode [general|
  scientific]` permits both directions and the UI selectors dispatch the same
  command; `Mode` carries requested/resolved/assurance/note but no downgrade
  marker, so a general-to-scientific upgrade is neither rejected nor
  recorded.
- (8) The stores domain-search note is stale: its status line still reads
  "draft reference note ... locked but not yet implemented tool contract"
  although the tool ships, and it still says "RAG/scientific mode" where it
  means "scientific mode's retrieval node".  The compulsory-node versus
  optional-tool distinction is already documented there; only the status and
  the phrasing need fixing.

## 5. Ranked open design questions

Ranked by design dependency.  "Blocking" means no scientific-mode
implementation can be decided before it; "layers" names what each answer
changes.

| # | Question | Blocking | Layers |
|---|---|---|---|
| 1 | Scientific graph topology: node set, edges, state fields, and its relation to the general graph (additive layer versus separate graph). | yes | backend graph/state, API events, web and TUI labels/panes, docs |
| 2 | Epistemic verifier: the same evaluator node with mode-conditional prompt/schema versus a separate node, plus its verdict contract.  `EvaluationSchema` has no groundedness/coverage/confidence field, so an epistemic output needs new fields; a single node is technically possible through the per-run dynamic output schema, which is exactly why "new node" needs an explicit decision. | yes | backend node/schemas/router, API verdict events, web and TUI rendering, docs |
| 3 | Compulsory retrieval node and how scientific mode uses RAG: domain choice, placement relative to query generation, failure UX and retry, evidence state shape. | yes | retrieval layer, backend, API, web inspector, docs |
| 4 | Provenance data model and no-bypass enforcement: evidence to step to execution to verification, with a scientific run unable to reach the answer without grounding and verification. | yes | backend state/schemas, API inspect/context, web and TUI provenance view, evaluation, docs |
| 5 | Assurance semantics: who sets `verified`, under what conditions, and whether two values suffice. | yes | backend state, API context/route payload, web and TUI label, docs |
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

Nothing in this note is a decision.  The sequence that follows from it is:
settle questions 1-10 (topology, verifier, retrieval node, provenance,
assurance, source definition, mode resolution, downgrade enforcement,
the answer variant and the approval mapping), then record the outcome as a
new ADR and ratify or rewrite ADR-0036.  Until then, scientific mode remains
undesigned and the general path remains the only implemented path.

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
   Status: proposed as the topology choice; blocked on the concrete node set,
   edges and state fields -- the correctness layer has no graph nodes and no
   state fields today (scientific-mode.md:16-17).

2. Epistemic verifier -- a separate, independent node rather than a
   mode-conditional branch of the operational evaluator.
   Rationale: ADR-0029 invariant 7 requires verification independent of
   generation (adr/0029:75); the operational evaluator "is not an epistemic
   verifier and must not be presented as one"
   (agent-topology-literature-review.md:180); ADR-0036 makes the scientific
   evaluator epistemic (adr/0036:60-62).  One node is technically possible
   through the per-run dynamic output schema (scientific-mode.md:145), which
   is why the choice needs a decision, but it weakens the independence claim.
   Status: proposed as node shape; blocked on the verdict contract, which
   needs groundedness/coverage/confidence fields `EvaluationSchema` lacks
   (scientific-mode.md:145).

3. Compulsory retrieval node and how scientific mode uses RAG -- a compulsory
   node on an unconditional edge that always runs and writes retrieved
   evidence into checkpointed state; the domain comes from the configured
   catalogue.
   Rationale: ADR-0029 invariant 1 (steps "retrieve or receive the evidence
   required" before execution, adr/0029:66) and invariant 3 (evidence
   inspectable before planning and before execution, adr/0029:68); the
   compulsory-node versus optional-tool distinction is already recorded
   (stores-domain-search-tool.md:4-6, :553-554, :562-564).
   Status: proposed as the placement rule; blocked on instantiation count,
   failure/retry UX and the evidence state shape (scientific-mode.md:146).

4. Provenance data model and no-bypass enforcement -- an evidence to step to
   execution to verification chain held as graph state, with the answer
   unreachable unless each claim is grounded and verified.
   Rationale: ADR-0029 requires every claim traceable to its sources
   (adr/0029:21) and attributed to a plan step and its evidence (invariant 6,
   adr/0029:74); invariants 1 and 11 make control flow workflow-owned so the
   model "cannot decide to omit required grounding" (adr/0029:66, :79);
   ADR-0030 forbids silent downgrade (scientific-mode.md:39).
   Status: proposed as the chain shape; blocked on the concrete data-model
   and schema shapes (scientific-mode.md:147).

5. Assurance semantics -- keep the two values; only a passing epistemic
   verification may set `verified`, everything else stays `unverified`.
   Rationale: ADR-0030 makes assurance structured state
   (scientific-mode.md:39); ADR-0036 reserves `verified` for the epistemic
   evaluator's passing results (adr/0036:60-62); the implemented literal is
   `unverified` and nothing sets `verified` (scientific-mode.md:61-62).
   Status: proposed as the rule; blocked on per-claim versus per-run
   assurance and on whether two values suffice while no verifier exists
   (scientific-mode.md:148).

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

12. Scientific evaluation harness -- status: blocked.  No verifier contract
    exists yet, so whether a `verified` claim can be validated against a task
    set cannot be answered (scientific-mode.md:155).  Depends on 2 and 5.

13. Frontend scientific UX -- status: blocked; deferred.  It "follows the
    API shapes" (scientific-mode.md:156), which depend on 1, 2 and 4.

14. Documentation hygiene -- status: proposed, and executable now: complete
    the five actions listed in section 6 (stores-domain-search-tool.md status
    and phrasing, evaluator docstring alignment, note indexing and C4
    caption, status at proposed-ADR citations, the ADR-0029/0030 status
    rule).
    Rationale: the concrete list at scientific-mode.md:159-175.

Remaining open questions: the blocked items above -- the exact node
set/edges/state fields (1), the verdict contract (2), retrieval placement,
retry and evidence shape (3), the provenance schema (4), per-claim assurance
and two-value sufficiency (5), source criteria and owner (6), auto-selection
(7), the downgrade mechanism (8), the citation schema (9), scientific-specific
HITL additions (10), the evaluation harness (12) and the frontend shapes (13).
These are still open, not decided; each needs the ADR of question 11 before
implementation.
