---
status: "proposed"
date: 2026-09-19
decision-makers: Ankur Sinha
consulted: "literature review (devdocs/system/agent-topology-literature-review.md)"
informed: klea contributors
---

# General (operational) agent path: topology, plan evolution, failure signals and escalation

## Context and Problem Statement

ADR-0030 scopes the correctness invariants of ADR-0029 (grounding, evidence
inspection, gating, independent verification, provenance) to Scientific mode,
and defines General mode as an explicitly lower-assurance operating mode.  The
topology of the general path has not been cemented.  ADR-0025's proposed
``plan -> explore -> toolpick -> observe -> evaluate`` loop was superseded by
ADR-0029 and was evaluated against a correctness bar that no longer binds
general mode.  The current ``klea_agent`` graph was prototyped in parallel with
``klea_rag`` before these decisions and is not authoritative.

Klea targets academics and non-corporate users without frontier-model token
budgets.  The general path must therefore be cost-efficient and must work with
non-frontier and small local models, while remaining operationally reliable.
A user may have exactly one model configured; requiring multiple models is not
acceptable.  Building the stores is already the only expected setup.

The literature review (`devdocs/system/agent-topology-literature-review.md`)
finds that flat ReAct is the easiest and most flexible shape but not the most
cost-efficient (ReWOO, LLM Compiler), that small models are weakest at exactly
what a single monolithic loop demands (so capability decomposition into focused
stages is what makes weak models viable), that as-needed decomposition (ADaPT)
is the efficient frontier, and that an external signal -- not a same-call
self-critique -- is required to judge success.

Question: what topology should the general agent path use, and how should it
detect failure and escalate?

## Decision Drivers

* Cost efficiency for token-limited users: cheap by default, no planning
  ceremony for trivial input.
* Correctness as operational task success/reliability, not epistemic
  verification (that is Scientific mode; ADR-0036).
* Non-frontier and small models: focused stages with single-responsibility
  prompts.
* A single configured model must be sufficient; model escalation is optional.
* No extra user setup beyond the stores.
* Inspectability (ADR-0013) and reuse of the shared picker/caller (ADR-0020)
  and graph/node templates (ADR-0016/0019).
* A clean boundary with Scientific mode: the general path MUST NOT enforce
  epistemic stages.
* Plan as evolvable state driven by step feedback.

## Considered Options

* A. Flat ReAct: one loop node with tools, answering or acting each turn.
* B. Rigid plan-and-execute: plan once (ReWOO/Plan-and-Solve), execute without
  replanning.
* C. As-needed operational loop (chosen): a cheap default with
  decomposition/replanning triggered by step failure.
* D. Retain the current prototype loop (``goal_setter -> explore_planner ->
  picker -> caller -> evaluator``) as the general path.
* E. Merged route+plan node: one call emits ``answer | act | plan`` and, for
  ``plan``, the plan itself.  Not chosen; retained as a harness experiment.
* F. Two-way route (``answer | plan``) with one-step plans for trivial tasks.
  Not chosen; retained as a harness experiment.

## Decision Outcome

Chosen option: **C. As-needed operational loop**, because it is the only option
that is cheap by default, adapts to the executor's capability, and stays within
a single model, while reusing the shared machinery and keeping the epistemic
boundary clean.

The general path is:

```
Guard
  -> RouteDecision (cheap LLM: answer | act | plan)
       +-- answer -> Answer (text produced inline; terminal)
       +-- act ----> Act
       +-- plan ---> GoalSetter -> Planner -> Act

Act (shared ToolsPicker + ToolsCaller; parallel calls within a step)
  -> TriageRouter (deterministic, per call)
       +-- error, retries left -> Act (re-pick with error)
       +-- retries exhausted   -> GoalSetter -> Planner (revise plan)
       +-- no error            -> Evaluator (operational)

Evaluator (runs after every Act batch; judge only)
  +-- step_incomplete -> Act
  +-- step_done       -> Act (next step)
  +-- need_replan     -> GoalSetter -> Planner
  +-- plan_done       -> AnswerFromResults -> Answer
```

When the verdict is ``plan_done``, ``AnswerFromResults`` synthesises the final
user answer from the goal, the completed plan and the observations; the
Evaluator never generates text.  Act, TriageRouter, Evaluator and Planner form
the work loop.  Every entry to
the Planner passes through GoalSetter, which sets the goal once and is skipped
if the goal is already set.  The Planner is re-entered from three places: the
initial `plan` route, the TriageRouter (ADaPT policy, repeated failure of the
same step), and the Evaluator (`need_replan`); after a revision it returns to
Act.

* **RouteDecision** decides upfront whether the query is answered directly,
  handled by a single act, or planned.  This is the plan-first tier: non-trivial
  queries enter the Planner before acting, avoiding a flailing first attempt
  (ReWOO/Plan-and-Solve evidence), without paying a planning call for trivial
  chat.  It does not replace as-needed decomposition.
* **Route classification** is judged from the request, the conversation and a
  coarse capability summary: ``answer`` needs no environment; ``act`` is one
  independent hop; ``plan`` has dependent steps, discovery, or a verifiable end
  state.  The boundary is a gradient, so misrouting must be cheap and
  recoverable; examples and the rubric are in the control-flow note.
* Trivial chat costs Guard plus one RouteDecision call: the route node answers
  inline (`answer`) with no planning and no tools.
* **Act** uses the shared ``ToolsPicker``/``ToolsCallerNode`` (ADR-0020) and
  supports parallel tool calls within a step via ``dispatch_tool_calls``.  Tool
  emission follows ADR-0034 (prompt injection with ``ToolCallsSchema``).
* **Tool disclosure is tiered** from the single ``tools_info``: RouteDecision
  sees a coarse capability summary (or nothing); the Planner sees compact
  entries (name plus docstring, without the parameter list); the ToolPicker
  sees full descriptions.  The Planner sees all configured tools (bundled plus
  domain); filtering is deferred, and if needed should prefer multi-domain
  selection or tool retrieval over single-domain classification.
* **Planner and ToolPicker stay separate nodes**: the Planner runs once (or on
  replan) without per-step outputs, while the picker runs after each batch with
  the latest results, so it can set arguments that depend on prior outputs.
  Merging them would force an upfront ReWOO-style plan of concrete calls and a
  large compound emission that small models handle poorly; the picker is also
  shared with RAG (ADR-0020).
* **Plan is state**: ``PlanSchema``/``StepSchema`` carry the ordered steps and
  their per-step ``success_criteria``; the plan is mutable and revised by the
  Planner when step feedback invalidates it.  There is no cross-step DAG
  scheduling; step order encodes dependencies.
* **The goal is immutable**: ``GoalSetter`` is the sole writer of the goal and
  task-level success criteria, and the Planner reads but never writes them, so
  a replan cannot move the success reference (which would otherwise let a model
  declare success by weakening the criterion).  On the ``act`` path the goal is
  unset and the Evaluator judges the single step seeded by RouteDecision; the
  goal is frozen as soon as planning begins.  Immutability is per task/run, not
  per session:
  ``InitGraphState`` resets the goal each turn, so a new request gets a fresh
  goal without a new session.
* **Failure signals are tiered and deterministic-first** (see the control-flow
  note): (0) structural/deterministic (emission validation, tool ``is_error``,
  artifact predicates, budget exhaustion); (1) observable-against-criterion;
  (2) independent LLM verification (a separate node, never same-call
  self-critique); (3) human.  General mode prioritises level 0 and treats
  levels 1-3 as operational.
* **Per-call failure triage**: transient failures are retried at dispatch
  (bounded); call-level failures (bad arguments, wrong tool, permission denial)
  return to the **picker** with the error fed back; step-level failures return
  to the **planner**.  The ADaPT policy applies: re-pick with the error for a
  bounded number of attempts of the same step, then escalate to the planner.
  Failure is attributed per call, not per batch.
* **Evaluator judges only** and runs after every Act batch, checking the goal
  and the current step against its criterion.  Its verdict is an explicit enum
  with literal next steps: ``step_incomplete`` -> picker; ``step_done`` ->
  picker for the next step; ``plan_done`` -> ``AnswerFromResults``;
  ``need_replan`` -> planner.  It never generates the user-facing answer: a
  separate synthesis stage (``AnswerFromResults``) does that on ``plan_done``,
  from the goal, completed plan and observations.  Keeping judgement and
  generation separate keeps each LLM call single-responsibility (robust for
  small models) and lets the same evaluator contract serve as the independent
  scientific verifier (ADR-0029 invariant 7).  The cost is one extra LLM call
  when a task completes; trivial chat is unaffected (RouteDecision answers
  inline).
* **Escalation has two axes**: structural escalation (same model, more process:
  planning, decomposition, retrieval, retries) is always available; model
  escalation (a stronger model) is optional and requires more than one
  configured model.  The single-model case is the default and must be fully
  functional.
* **Model assignment is static per role** (``llm_models``), for example a
  stronger planner and a cheaper caller where configured.  There are no
  ordered model cascades: a reliable "this model cannot do this" signal does
  not exist, so automatic model escalation is not implemented.
* **Retrieval is an optional tool** (a lightweight store/web search tool), not
  the full RAG graph, and is not mandatory in general mode (ADR-0036).
* **No multi-agent**: a single thread with parallel tool calls and an evolvable
  plan.  A different model may be used for the evaluator role, but that is a
  role assignment, not a second agent.

Scientific mode is the general base plus the ADR-0029 correctness layer,
inserted additively; the general path contains no
retrieval/evidence/gating/verification/provenance stages (ADR-0036).

### Consequences

* Good, because trivial input stays at two LLM calls and planning is paid only
  when complexity requires it.
* Good, because decomposition into focused stages is what makes non-frontier
  and small models viable, and the topology works with exactly one configured
  model.
* Good, because planning, tool calling and evaluation reuse shared
  ``klea_utils`` nodes and remain inspectable (ADR-0013).
* Good, because the plan is evolvable state, so multi-step tasks adapt to step
  feedback without a DAG scheduler.
* Good, because evaluation and answer generation are separate stages, so the
  evaluator stays an independent judge that can be reused as the scientific
  verifier (ADR-0029 invariant 7).
* Bad, because a completed task costs one extra LLM call (the answer synthesis
  stage); trivial chat is unaffected.
* Good, because the goal and task-level success criteria are set once and
  cannot be rewritten by replanning, so the Evaluator's success reference is
  not under the control of the generator.
* Good, because the operational/epistemic boundary is structural: scientific
  stages are absent from the general path.
* Bad, because the general graph has more nodes and routing than flat ReAct,
  and both the triage policy and evaluator prompts must be tuned.
* Bad, because the operational evaluator can be wrong; general-mode results are
  therefore labelled unverified.
* Bad, because the quality of the failure signal depends on the planner
  emitting meaningful per-step success criteria.
* Neutral, because retrieval is available but optional, and its cost is not
  incurred for tasks that do not need it.

### Confirmation

* Graph shape: the compiled general graph is ``Guard -> RouteDecision ->
  {answer | act | plan}`` with the work loop ``Act -> TriageRouter ->
  Evaluator -> {Act | Planner | Answer}``; GoalSetter gates every Planner
  entry; and no mandatory retrieval/evidence/gating/verification/provenance
  stage is present.
* Tests: the deterministic triage router's retry policy and the evaluator's
  verdict-to-route mapping.
* Schema: ``StepSchema`` has ``success_criteria``; the evaluator verdict is a
  pydantic model with literal next-step values.
* Harness: the evaluation harness compares this topology against flat ReAct on
  task success, LLM calls, tokens and wall-clock
  (`agent-evaluation-harness.md`).
* Review checkpoint: the Act node uses the ADR-0034 emission mechanism; no
  ``bind_tools``.

## Pros and Cons of the Options

### A. Flat ReAct

* Good, because the simplest graph, fewest nodes, maximal flexibility.
* Bad, because interleaved loops are the least token-efficient shape (ReWOO
  5x, LLM Compiler 6.7x).
* Bad, because a single weak model is asked to plan, pick, call, summarize and
  self-check in one context.
* Bad, because planning and tool generation are conflated and the pipeline is
  inspectability-poor.

### B. Rigid plan-and-execute

* Good, because plan-first is cheaper and Plan-and-Solve/ReWOO beat interleaved
  ReAct.
* Bad, because it loses mid-plan adaptivity; our requirement is an evolvable
  plan driven by step feedback.

### C. As-needed operational loop (chosen)

* Good, because cheap default, capability-adaptive, single-model, inspectable,
  boundary-clean.
* Bad, because more nodes and tuning than flat ReAct.
* Bad, because the operational evaluator is fallible.

### D. Retain the current prototype loop

* Good, because it exists.
* Bad, because it was prototyped without ADRs and evaluated against no bar; it
  adds exploration stages and is not the agreed direction.

### E. Merged route+plan node

* Good, because one fewer LLM call on complex tasks and a simpler graph.
* Bad, because it forces one model and one schema to do routing and planning;
  a compound structured emission is unreliable for small models; and trivial
  chat pays a planner-sized call.

### F. Two-way route with one-step plans

* Good, because a simpler route schema and uniform per-step success criteria.
* Bad, because trivial commands pay a planner call, losing the cheap-route
  benefit.

## More Information

* Evidence: ``devdocs/system/agent-topology-literature-review.md``.
* Companion mechanics: ``devdocs/system/agent-general-path-control-flow.md``
  (to be replaced by the C4 agent component diagram once implemented).
* Related: ADR-0020 (unified picker/caller), ADR-0013 (inspection),
  ADR-0016/0019 (graph and node templates), ADR-0028 (prompt cache),
  ADR-0033 (per-request model overrides), ADR-0034 (tool emission),
  ADR-0036 (grounding enforcement), ADR-0029/0030 (scientific mode and modes).
* Alternatives to test: the merged node (E) and the two-way route (F) are
  compared against the chosen topology C in the evaluation harness, alongside
  the question of whether the ``act`` branch earns its keep versus one-step
  plans.
* Supersedes: no prior ADR; the general path was not decided by ADR-0025
  (superseded by ADR-0029 for the correctness loop).

## Update (2026-09-10): narrow router + task-only Planner

The original decision kept a cheap `RouteDecision` first hop plus a separate
`GoalSetter`.  It was first simplified to a single-entry Planner that both
routed and planned; live testing then showed that let the Planner answer
environment questions inline from assumption (it hallucinated a cwd), which
prompting alone cannot reliably prevent.  The final shape is a **narrow,
fail-closed router** plus a **task-only Planner**: `GoalSetter` is removed, and
`RouteDecision`/`RouteSchema` are reinstated with a smaller job.

* `RouteDecision` runs after the Guard and decides `chat`, `continue`, or
  `task`; it is tool-free, reads conversation history for follow-ups, answers
  `chat` inline, and **defaults to `continue` when unsure** (fail-closed)
  (2026-10-22: was `task`; see the ADR-0054 intent-routing update).  It is the
  only place a model may answer the user directly, and it must not answer
  world-facts.  `chat` is a self-contained conversational/general-knowledge
  reply (including direct conversational edits such as shortening or
  rephrasing); a request that derives from earlier work/turns (summarise,
  compile, compare, synthesise) is `continue`, so it is planned/checked and its
  deliverable persisted rather than answered inline from the lossy conversation
  (update 2026-10-12; route 2026-10-22).
* The `Planner` (task path only) emits `PlannerOutput { goal, plan }` and never
  answers the user.  `plan.status` is the post-Planner routing source:
  `in_review` -> `AwaitHuman` (review), `needs_input` -> `AwaitHuman` (input),
  `no_plan_required` -> answer (success; no execution needed; removed
  2026-10-19, see the update below),
  `unplannable` -> failure answer, `in_progress` -> step execution.  It
  produces a plan even for a single action.
* Trivial chat stays the cheap floor: Guard + RouteDecision (2 calls).
* Goal immutability: the Planner writes `state.goal` only while unset;
  `InitGraphState` clears it per execution.
* **Plans are tool-executable only**.  There is no reasoning-step node:
  reasoning happens in the router (chat), the Planner (decomposition), the
  picker (arguments) and the evaluator/answer synthesis.  The picker is the
  sole selector of the concrete call; the step's `suggested_tools` is a prior it
  honours when they fit and deviates from (with a reason) when they do not.
  The contract is the step's success criteria, not the tool identity.
* **An empty picker selection is a picker failure, not a planner
  failure** (amended 2026-09-18).  "Empty" means *no usable call*: an empty
  list, or a list whose calls all have empty/whitespace names.  Such a
  selection leaves the Planner little to change, so replanning would mostly
  loop blindly.  Instead the picker is retried a bounded number of times (its
  previous failure is fed back into the prompt); after the budget, the round
  proceeds through the caller (which dispatches nothing usable) to the
  Evaluator, whose `need_replan` carries the failure back to the Planner as
  feedback.  There is deliberately **no** Planner-level retry for "no
  suggested tools": the picker is the authority on suitability and may solve
  such a step anyway; if it cannot, the Evaluator feedback path lets the
  Planner revise the step or return `unplannable`.
* **Invalid tool names never reach the server** (amended 2026-09-18).  A weak
  model can emit an empty `tool` name or one outside the disclosed catalogue.
  Dispatch rejects such calls with a synthetic `is_error` result (no server
  call), so the failure is visible to the retry/evaluation loop.  Empty/
  whitespace names are handled as picker failures (above) and retried before
  dispatch; unknown-but-non-empty names are not retried by the picker but are
  caught by dispatch, whose error Triage acts on.  The picker itself does not
  filter, so the feedback is preserved.
* Plan review uses a human-input node (`AwaitHuman`, ADR-0046) that pauses with
  LangGraph `interrupt` and captures an explicit approve/revise decision.  A
  human approval deterministically sets `in_progress` and dispatches straight
  into execution; a revision returns the feedback to the Planner, which owns
  the revised plan.  (This supersedes the earlier stub and the note here that
  the Planner LLM performs the `in_review -> in_progress` transition: a human
  approval now does.)
* Deterministic budgets bound the loop (counters in state, enforced in the
  acting nodes): tool-error re-picks (`tool_retry_counts` -> triage replan;
  6 re-picks), repeated non-advancing evaluations (`step_attempt_counts` ->
  replan; 6 attempts), Planner entries (`automated_plan_revisions` -> preserve
  the plan and ask the human for guidance via `needs_input`; 8 automated
  replans).
  The plan length itself is deliberately unbounded: long-horizon research
  tasks may need many focused steps, and the per-step/replan budgets bound the
  execution loop.
  *(Amended 2026-09-29: the earlier global `tool_rounds` -> `abort` cap on
  total picker+caller rounds was removed - it false-aborted legitimate
  long/deep or multi-replan plans.  Amended 2026-10-20: the fixed 30-step
  plan-size cap was also removed for long-horizon tasks.)*  Consecutive empty
  picker selections are bounded in the picker node itself (`picker_attempts`,
  reset on a successful pick or a step change); after the budget the empty
  round proceeds to the Evaluator.
* Feedback is split by source: `evaluation` (LLM judge, structured) and
  `human_feedback` (review text); both reach the Planner, and run progress
  (query, plans, verdicts, answers) is recorded in `messages`.
* **The Planner owns the whole plan; code does not mutate it** (amended
  2026-09-18).  The Planner authors step statuses and `current_step_index`
  as well as step content.  The earlier deterministic re-application of
  completion markers by matching step numbers across plans was removed: on a
  replan where the model renumbers or merges steps, a stale `done` number can
  collide with a new step and force it `done`, leaving `current_step_index`
  out of range while per-step outputs were cleared (an unrecoverable picker
  loop).  Consistency is now enforced structurally: `PlanSchema.validate_plan()`
  checks positive/unique step numbers, in-plan `depends_on` references and an
  in-range `current_step_index`; a violation triggers a bounded re-invoke
  through the generic node validation-retry loop (error exposed as
  `validation_feedback`), then fails closed to `unplannable`.  Machine-checkable
  invariants live in schema validation, not in prompt rules or positional
  patches.
* **`abort` records unreachability honestly** (amended 2026-09-18).  `abort` is
  a first-class Evaluator verdict for a goal the observations already prove
  unreachable (for example a read-only task whose required input does not
  exist), routing straight to the failure answer instead of burning the replan
  budget on a dead end.  The model's reason is carried into `failure_reason`.
  *(Amended 2026-09-29: `abort` is now always a model judgement - the
  budget-triggered abort was removed with `tool_rounds`.)*  Reachability is a
  judgement against evidence, deliberately not a plan-similarity check (two
  plans cannot be compared deterministically).
* `AnswerFromResults` synthesises the final reply on success and explains the
  failure (from `failure_reason`) on `abort`/`unplannable`.
* Residual risk: a router misroute (a reality question answered as chat).  The
  mitigations are the narrow fail-closed prompt; a deterministic env-keyword
  backstop and an independent inline-answer verifier are deferred (the latter
  is needed for scientific mode anyway, ADR-0029).  A general `run_command`
  tool (deferred) makes environment facts answerable via the task path.
* A deterministic `read_only | full` access level is deferred to its own ADR.
* Amended `2026-09-14`: both deferrals are now implemented -- the access
  level in ADR-0037 (full-mode default) and the `run_command` tool in
  ADR-0038 (destructive, full-mode only).

The current mechanics live in
`devdocs/system/agent-general-path-control-flow.md`.

## Update (2026-09-19): reasoning steps, tool-identity contract and state persistence

Design review amendments to the general path.  The affected clauses of the
decision and its earlier updates are named explicitly.

### Reasoning steps (supersedes "Plans are tool-executable only")

The 2026-09-10 update stated "Plans are tool-executable only.  There is no
reasoning-step node" (`:320-325`).  That is **superseded**.  Real tasks
(academic, coding, long-horizon) contain steps whose output is a
*judgement* rather than an external observation -- interpretation of
results, decision/selection, hypothesis or experimental-design formation,
intermediate synthesis.  Those conclusions must be first-class so later
steps can consume them.

* `StepSchema` gains `kind: Literal["tool", "reasoning"]`.
* Tool steps name at least one tool from the disclosed catalogue; reasoning
  steps name none.
* A new `ReasoningNode` (an LLM node) reads the goal, plan, observations
  and current step and emits a general text conclusion.  It never answers
  the user, and the picker/caller are skipped for it.  Its result is stored
  as a `StepOutput` alongside tool results -- to the Planner it is just
  another step: `StepOutput.result: CallToolResult | str`.
* Reasoning output is deliberately general, not forced structured;
  `ArtefactSchema.type_`/`metadata` carry more where it exists.
* This does not duplicate the Planner: the Planner reasons about *plan
  structure* (what to do next), not about *what is true*.

### Tool-identity contract (narrows the picker's role; ADR-0020)

* The Planner selects tool identity per step via `suggested_tools`; the
  picker **binds arguments only** and never substitutes another tool.  The
  "sole selector of the concrete call" wording (`:322-325`) is narrowed to
  argument binding.
* Disclosure stays the **full, static catalogue** rather than a per-step
  subset: the per-step restriction lives in the picker prompt, so the
  cacheable system prefix is preserved (ADR-0028).  A deterministic
  substitution backstop is deferred until drift is observed.
* Picker inputs are trimmed to the tool list, all observations
  (`observations_text()`), the current step and its feedback.  It does not
  need the goal or other big-picture context.
* Retries are for execution/emission errors on the **same** tool with new
  arguments -- never a search for a different tool.
* An unusable selection is recorded as a synthetic `is_error`
  `CallToolResult` (the standard result shape) and, after a bounded
  emission retry, routes `Binding tool calls -> Planning` directly.  This
  supersedes the empty-selection rule at `:326-337` (empty -> caller ->
  Evaluator -> `need_replan`), which spent a caller dispatch and an
  Evaluator call on a batch with nothing to run.  Call-level tool errors
  still reach the Planner directly via the TriageRouter, carrying the raw
  error in `observations`.
* When a round completes without error, the step's earlier errored
  `StepOutput` entries are pruned before the new results are appended, so
  superseded failures do not confuse the Evaluator or the answer synthesis.

### Persistence: plan-scoped working memory vs session-scoped artefacts

State lifetime is explicit; the aim is a bounded working set, not the
"keep everything" of a flat ReAct loop.

* Working memory is **plan-scoped**: `step_outputs` (tool results and
  reasoning conclusions) is visible to later steps in the plan, then
  cleared at run start (`InitGraphState`) and when the Planner authors a
  new plan.
* `artefacts` is **session-scoped**: `InitGraphState` no longer clears it.
  The completed task's deliverable is auto-persisted by
  `AnswerFromResults` as a concise `ArtefactSchema` (`content` = the
  result, `metadata` = goal/provenance/references, `type_` = category), and
  the answer-composer prompt renders it (`artefacts_text()`) so a later task
  can build on it.  The Planner does not receive artefacts (2026-10-19); it
  works from conversation memory.  This extends the artefact model of
  ADR-0029 (evidence and provenance).
* The rendered artefact block is **bounded** so a long session cannot grow
  the answer-composer prompt without limit: `artefacts_text()` selects the
  most recently written entries up to `MAX_ARTEFACTS_IN_PROMPT` (20) and
  `MAX_ARTEFACTS_PROMPT_CHARS` (12,000), then renders them in chronological
  order and notes how many older entries were omitted.  A superseded id
  (same goal) is re-inserted at the end, so a re-run counts as newly written.
  The checkpoint still retains every artefact (session export can use them);
  only the prompt block is bounded.
* `messages` remains lossy continuity (summarised, ADR-0018), not a durable
  store.  The deliverable appearing in both is expected: one is the
  conversational trace, the other the durable, addressable result.
* There is deliberately no `output_scope` on steps: the Planner cannot
  predict whether a later task will need a result, so durability is not its
  call.  Explicit persistence on user request (an `add_artefact` tool) is
  deferred.
* Cross-run transfer therefore happens only through `artefacts` (precise)
  and `messages` (lossy); step data does not enter the next task's context
  unless promoted.

### Parallelism

Step granularity, explicit dependencies and parallel execution are a
separate decision: ADR-0041 (accepted 2026-09-21), which supersedes the "no
cross-step DAG scheduling" clause above.  A reasoning step is effect-free and
therefore parallelisable under that decision.

## Update (2026-09-21): plan schema direction and plan-history counters

The plan model is split so the authored plan is the base and the runtime plan
extends it:

* ``PlannerPlanSchema`` is now the base: the steps, the statuses the Planner may
  set (``in_progress``/``in_review``/``needs_input``/``unplannable``), and
  ``current_step_index``, plus the shared ``render``/``current_step``/
  ``validate_plan`` behaviour.
* ``PlanSchema(PlannerPlanSchema)`` widens ``status`` with the lifecycle values
  written by code and adds the run-history counters.  This direction (authored
  plan as base, runtime bookkeeping as derived) is the inverse of the earlier
  subclass-for-the-model arrangement; the Planner's structured-output schema is
  unchanged.

Plan-history counters live on the plan, not as floating state fields:

* ``plan_version`` -- monotonic count of plans the Planner authored in the run
  (0 = no plan; the first authored plan is 1);
* ``human_feedback_rounds`` -- monotonic count of human review rounds processed;
* ``automated_plan_revisions`` -- renamed from ``plan_revisions``; consecutive
  automated replans since the initial plan or last human review (the budget
  counter, reset on review).

These are a **context-free signal**, not content.  ``AnswerFromResults`` is told
the final plan is version V after M human review round(s) (``revision_summary``)
so it does not claim the plan was executed unilaterally, but it is not given the
raw feedback or earlier plan versions: feedback is only meaningful attached to
the plan version it refers to, which the answer does not receive.  A future
verifier that needs the full path (e.g. scientific mode, ADR-0029) would require
a retained plan-revision journal; that was added in the 2026-10-21 update below
for the Planner and guidance HITL, but is still not given to the answer
synthesis.

``human_feedback`` and ``replan_reason`` remain separate **transient** state
fields: node-to-node signals consumed (cleared) within the run, not durable
state and not rendered into downstream prompts.

Cross-references: ADR-0018 (message memory), ADR-0020 (picker/caller),
ADR-0028 (prompt cache), ADR-0029 (correctness and artefacts), ADR-0032
(state ownership), ADR-0041 (parallel step execution).

## Update (2026-10-02): goal-authoritative completion and explicit step resolution

Plan completion is now **goal-authoritative**: ``plan_done`` asserts the user's
goal is met, and the plan is completed only when no step is ``pending``.  A step
that is not needed is resolved explicitly with the new ``step_skipped`` verdict
(a distinct ``skipped`` step status), so early completion is legal but never
silent.  The old behaviour of force-marking every pending step ``done`` on
``plan_done`` is removed.

``plan_done`` with a pending step, or an all-resolved plan with no overall, is a
machine-checkable inconsistency: the Evaluator retries once with the reason
exposed as ``validation_feedback`` and, if the model insists, fails closed
(``plan_done`` + pending -> Planner; all resolved + no overall -> answer).  See
``devdocs/system/agent-general-path-control-flow.md``.

## Update (2026-10-12): `no_plan_required` (no-execution) plans

> Superseded by the 2026-10-19 update below: the status and the zero-step
> bypass have been removed.

A task may need no environment work at all -- every fact its goal needs is
already available (for example in the session ``artefacts`` gathered by earlier
tasks).  The Planner now signals this with a new ``no_plan_required`` status (no
steps) instead of being forced into ``unplannable``.  Unlike ``unplannable``
(the goal cannot be met), ``no_plan_required`` is a **success**: the Planner
still sets the goal, the run routes to ``AnswerFromResults`` as success, and the
answer-composer writes the reply from the available artefacts/observations.

Mechanics: ``Planner._update_state`` excludes ``no_plan_required`` from the
empty-plan coercion (which keeps failing any other no-step status closed as
``unplannable``); ``_validate_result`` rejects ``no_plan_required`` with steps
and requires a goal; ``_planner_router`` maps it to the answer edge.  The task
type (report vs synthesis) is carried by the goal and its success criteria, not
by the status -- so the answer-composer needs no separate synthesis mode.  See
``devdocs/system/agent-general-path-control-flow.md``.

## Update (2026-10-19): `chat` plans return to the entry router

The narrow entry router can call a conversational turn ``task`` (it is
fail-closed).  When that happens the Planner can find no task at all -- for
example a follow-up that only needs the conversation history.  Previously it
had to choose between ``no_plan_required`` (which routes to
``AnswerFromResults``; that node has no memory, so the reply lost the
conversation) and ``unplannable`` (a false failure).

A new ``chat`` plan status separates the two: ``no_plan_required`` means "a
real task whose facts are already available", while ``chat`` means "not a task
-- the request was misrouted and needs the conversation".  A ``chat`` plan
carries no steps and no goal; the graph routes it back to ``RouteDecision``,
which is forced (via a prompt override and a router check on
``plan.status == "chat"``) to answer as chat from conversation history.  The
answer-composer stays off this path because it deliberately has no memory.

This also removes the misclassification failure mode: an empty plan is no
longer coerced to ``unplannable`` when the Planner meant to hand back a
conversational turn.  `_validate_result` rejects ``chat`` with steps;
`_update_state` fails closed to ``unplannable`` if the model insists after the
validation retries.  On the forced re-entry, ``RouteDecision._validate_result``
requires a ``chat`` route with a non-empty answer, so an ignored override
triggers the validation retry and, if the model still refuses, the routing is
still forced to ``chat`` (the delivery node's empty-answer fallback applies).
See ``devdocs/system/agent-general-path-control-flow.md``.

## Update (2026-10-19): `no_plan_required` removed; already-known facts use a reasoning step

`no_plan_required` let the Planner emit a zero-step plan when it judged the goal
answerable from information it could see.  That created an information
asymmetry: the Planner has conversation memory, but `AnswerFromResults` does
not, so a status decided from history could reach an answer-composer that could
not see the same facts.  It also bypassed the plan/evaluation loop and made the
Planner's output depend on a source (`artefacts`) that is no longer rendered
into its prompt.

The status is removed.  An `in_progress` plan must carry at least one step;
when the facts a goal needs are already known (conversation history,
observations, discovery), the Planner plans a single `reasoning` step that
synthesises them.  `ReasoningNode` has memory, so its conclusion lands in
`observations`, which `AnswerFromResults` does see, keeping the decision and
the composition on the same information contract.  The cost is one reasoning
plus one evaluator call on that path (previously none); the benefit is that a
"no work needed" decision can no longer strand the answer-composer without the
facts.

`_validate_result` rejects an empty `in_progress` plan and later retries are
coerced to `unplannable` (a failure) as before.  The Planner prompt no longer
receives the `artefacts` block: `artefacts` remain the answer-composer's
session-scoped cross-task record, but are no longer load-bearing for planning.
In-run completion is unchanged and uses the Evaluator's `plan_done` ->
`completed` path, not a Planner status.

See `devdocs/system/agent-general-path-control-flow.md`.

## Update (2026-10-20): tool identity is enforced by the binder

The tool-identity contract above (Planner selects, picker binds, no
substitution) is now enforced in code rather than left to prompt guidance.
The agent uses `ToolCallBinder`, a `ToolsPicker` subclass configured with
`bind_planned_steps=True`:

* One binder invocation covers one contiguous group of batch steps whose
  `suggested_tools` are an identical set (capped at 4 steps).  The prompt,
  schema and tool descriptions expose only that set; the binder cannot select
  a tool the Planner did not suggest.  Different sets are never unioned.
* The output is validated deterministically against each step's suggested
  tools.  Retries are bounded (2), after which the binder fails closed with a
  deliberate `no_tool` reason that returns the run to the Planner.
* Calls accumulate in `binding_calls`; `bind_next` loops the binder until all
  batch steps are bound, then the caller dispatches the accumulated calls in
  parallel (ADR-0041).  RAG keeps the shared `ToolsPicker` over its full
  domain set.
* The earlier "full static catalogue / deferred substitution backstop"
  trade-off is superseded for the agent.  Losing the stable tool block in the
  provider prefix cache (ADR-0028) is accepted in exchange for the contract.
* The Evaluator remains an outcome judge and does not police tool identity: a
  different tool may still satisfy a step's success criteria.  The binding
  contract is enforced before dispatch.

See `devdocs/system/agent-general-path-control-flow.md`.

## Update (2026-10-20): granular plans, no step cap

The Planner prompt now defines a step as one action with one observable
outcome.  It names one tool identity per tool step (several only as genuine
alternatives), splits independent reads/actions, and requires a reasoning step
to answer one bounded question rather than produce a report or the final
answer.  "Fewest steps" now means fewest *focused* steps, not fewest by
merging independent work.

`Planner.max_plan_steps` is removed: long-horizon research plans may have many
steps.  Runaway execution is still guarded by the per-step attempt budget and
the automated replan budget, which are independent of plan length.  A single
over-large plan can still cost tokens, so the granularity rules are the
quality guard, not a hard length limit.

The Reasoning node also starts with the full output-token ceiling (65536,
provider-clamped) because reasoning models spend most of the completion budget
on hidden reasoning tokens before emitting the conclusion.

## Update (2026-10-20): failed plans stay visible; exhausted budgets ask for help

A code-generated failure no longer replaces the plan with an empty
`PlanSchema`.  Validation exhaustion and a Planner-decided `unplannable` keep
the last authored `step_list` and counters, changing only the status (and
`failure_reason`); the status pane therefore still shows what was planned and
how far it got.

Replan-budget exhaustion is not a final failure.  The Planner preserves the
steps, appends a blocked "Provide guidance to continue" step carrying the last
replan reason, and writes `needs_input`.  The ordinary input HITL node asks the
user; its answer re-enters the Planner with human guidance, and because the
plan is a `needs_input` draft with no `replan_reason`, the automated revision
counter resets.  A cancel keeps the steps and marks the plan
`user_cancelled`.  This reuses `needs_input` rather than adding a new status or
HITL kind.

## Update (2026-10-21): retained plan-revision journal and step feedback

The deferred plan-revision journal now exists as `plan_history`: a FIFO of the
last 9 superseded plan versions (full steps plus the reason each was abandoned),
with the active plan kept separately in `plan`.  It is retained across automated
replans, human input and review revisions within a run, and cleared by the
task/continue boundary (`ResetState`, ADR-0054).  The
Planner prompt renders the previous versions and instructs the model not to
repeat an abandoned approach and to state what changed.  The answer synthesis
still receives only the context-free revision counters.

There is deliberately no deterministic repeat detector.  Differently worded
plans can describe the same failed approach, and exact text/structure
comparison would miss that or reject legitimate revisions.  The system relies
on giving the Planner the history, bounds the loop with the automated-replan
budget, and hands the decision to a human at exhaustion.

On budget exhaustion the input HITL ask includes the previous and active plan
sections so the user can see what was tried and failed; the answer re-enters
the Planner as `human_input`, resets the automated budget, and keeps
`plan_history`.  Routing stays on `needs_input`; it does not become
`unplannable`.

`step_incomplete` now also carries the Evaluator's reason back to the actor
that re-runs the step.  The binder and reasoning node render it as
`step_feedback` (the binder combines it with validation, failed-call and empty-
pick feedback).  The Planner clears the `evaluation` snapshot when it writes a
new plan, so feedback from the abandoned plan cannot leak into the next one.

The per-turn task boundary has since moved out of `InitGraphState`:
`RouteDecision` distinguishes `task`, `chat`, and `continue`, and `ResetState`
applies the full task reset or the continuation-oriented reset.  A
`continuation_evidence` record carries a bounded, feedback-style summary of
the previous run across graph invocations; `continue` re-plans for a new goal
from that evidence.  See ADR-0054.  `InitGraphState` now only seeds per-turn
fields and appends the query, leaving task state intact for routing.
