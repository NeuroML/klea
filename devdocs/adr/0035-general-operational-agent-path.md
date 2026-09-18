---
status: "proposed"
date: 2026-09-10
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

* `RouteDecision` runs after the Guard and decides only `chat` vs `task`; it is
  tool-free, reads conversation history for follow-ups, answers `chat` inline,
  and **defaults to `task` when unsure** (fail-closed).  It is the only place a
  model may answer the user directly, and it must not answer world-facts.
* The `Planner` (task path only) emits `PlannerOutput { goal, plan }` and never
  answers the user.  `plan.status` is the post-Planner routing source:
  `in_review` -> `AwaitReview`, `unplannable` -> failure answer, `in_progress`
  -> step execution.  It produces a plan even for a single action.
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
* Plan review uses a human-input node (`AwaitReview`) whose free-text feedback
  the Planner interprets; the Planner owns the `in_review -> in_progress`
  transition.  The first stage ships an auto-approve stub; real LangGraph
  `interrupt`/resume is recorded as ADR-0037.
* Deterministic budgets bound the loop (counters in state, enforced in the
  acting nodes): tool-error re-picks (`tool_retry_counts` -> triage replan),
  repeated non-advancing evaluations (`step_attempt_counts` -> replan),
  Planner entries (`plan_revisions` -> `unplannable`) and total picker+caller
  rounds (`tool_rounds` -> `abort`).  Consecutive empty picker selections are
  bounded in the picker node itself (`picker_attempts`, reset on a successful
  pick or a step change); after the budget the empty round proceeds to the
  Evaluator.
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
  budget on a dead end.  The model's reason is carried into `failure_reason`;
  only a budget-triggered abort is labelled as such.  Reachability is a
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
