# General-path control flow

Status: design note, not an ADR.  Concrete mechanics for ADR-0035; to be
replaced by the C4 agent component diagram once the graph is implemented.
Written 2026-09-10 by opencode (model: deepseek-flash); revised same day to
the narrow-router + task-path topology; revised 2026-09-19 for reasoning
steps, the tool-identity contract, the unified replan reason, ``needs_input``
and session-scoped artefacts (ADR-0035 update 2026-09-19).

## Scope

Mechanics of the general (operational) path: nodes, state, failure triage,
plan review, evaluator routing and escalation.  Scientific mode is the general
base plus the ADR-0029 correctness layer (ADR-0036).

## Control flow

```
START
  |
InitGraphState
  |
ModeDecision --(cannot run)--> ModeInformer -> END
  |
Guard --(unsafe)--> DecliningQuery -> END
  |
RouteDecision (narrow, fail-closed: chat | task; answers chat inline)
  |-- chat --> AnswerUser -> END
  '-- task --> Planner
                 |-- status unplannable ---> AnswerFromResults (failure) -> AnswerUser -> END
                 |-- status in_review -----> AwaitReview --(user input only)--> Planner
                 |-- status needs_input ---> AnswerFromResults (question) -> AnswerUser -> END
                 '-- status in_progress ---> step entry (dispatch by step kind)

Step entry (the Planner selects the tool; the picker binds arguments):
  kind = tool:
    ToolsPicker --usable call(s)------> ToolsCaller -> TriageRouter
                --deliberate failure--> Planner   (synthetic error observation + replan_reason)
                --empty (glitch)------> ToolsPicker (bounded retry), then Planner
  kind = reasoning:
    ReasoningNode -> Evaluator

TriageRouter (deterministic, tool-error triage):
  error, retries left  -> ToolsPicker
  retries exhausted    -> Planner
  no error             -> Evaluator

Evaluator (operational judge):
  step_incomplete -> step entry (current step, by kind)
  step_done       -> step entry (next step, by kind)
  need_replan     -> Planner
  plan_done       -> AnswerFromResults (persists the deliverable) -> AnswerUser -> END
  abort           -> AnswerFromResults (failure) -> AnswerUser -> END
```

Trivial chat: Guard + RouteDecision (2 calls); the router answers inline and the
graph goes straight to ``AnswerUser``.  ``RouteDecision`` is deliberately
narrow and tool-free -- it only decides "self-contained conversation vs needs
the environment", defaulting to ``task`` (fail-closed) so a world-fact is never
answered from assumption.

## Nodes

| Node | Role | LLM |
|------|------|-----|
| InitGraphState | seed/reset state | no |
| ModeDecision / ModeInformer | resolve mode (ADR-0030) | no |
| Guard | safety classification (ADR-0010) | yes (guard role) |
| RouteDecision | narrow entry router: ``chat`` (answer inline) vs ``task`` (fail-closed) | yes (chat role) |
| Planner | task-path brain: write the immutable goal, create/revise the plan, flag review (ADR-0035); never answers the user | yes (plan role) |
| AwaitReview | capture human review input only (no LLM); free-text feedback | no |
| ToolsPicker + ToolsCaller (Act, ``kind=tool``) | bind arguments for the step's suggested tools and dispatch (ADR-0020/0034); never substitute a different tool | picker yes, caller no |
| ReasoningNode (``kind=reasoning``) | produce the step's conclusion from the goal/plan/observations; records a ``str`` step output; no picker/caller | yes (chat role) |
| TriageRouter | deterministic tool-error triage: error present? retries left? | no |
| Evaluator | operational judge only: goal + step criterion; explicit verdict enum | yes (chat role; separate node from Act) |
| AnswerFromResults | synthesise the user answer (success) or the failure explanation | yes (chat role) |
| AnswerUser | deliver the final user-facing message | no |

## Entry routing and Planner outcomes

``RouteDecision`` emits a ``RouteSchema`` and is the only place a model may
answer the user directly:

| route | meaning | edge |
|-------|---------|------|
| ``chat`` | self-contained conversation/knowledge; the router wrote ``answer`` | ``AnswerUser`` |
| ``task`` | needs the environment/workspace/session | ``Planner`` |

The Planner (task path only) emits ``PlannerOutput {goal, plan, reason}``;
``plan.status`` is the post-Planner routing source:

| status | meaning | edge |
|--------|---------|------|
| ``in_review`` | plan produced, awaiting human review | ``AwaitReview`` |
| ``needs_input`` | blocked on a missing fact only the user can supply; the question is in ``reason`` (a partial plan is allowed) | ``AnswerFromResults`` (question) |
| ``unplannable`` | no viable plan | ``AnswerFromResults`` (failure) |
| ``in_progress`` | ready; execute the current step by its ``kind`` | step entry |

``Planner._update_state``: ``needs_input`` -> state ``needs_input`` with
``pending_question`` from ``reason``; steps + review -> ``in_review``; steps ->
``in_progress``; ``unplannable`` (or no steps) -> ``unplannable``.  It never
answers the user.  ``PlannerPlanSchema`` exposes only these editable statuses to
the model; the state ``PlanSchema`` also carries the runtime ones
(``not_started``/``completed``/``failed``/``aborted``) written by code.

### Planner plan ownership (Design A)

The Planner is the **sole author of the whole plan**, including each step's
``status`` and the ``current_step_index``.  Code does **not** mutate the plan:
there is no re-application of completion markers and no recomputation of the
current step by matching step numbers across plans.  That positional matching
was brittle -- on a replan the model may renumber or merge steps, so an old
``done`` step ``1`` could collide with a new step ``1``, be wrongly forced
``done``, and leave ``current_step_index`` past the end of the list while
``step_outputs`` were cleared (observed: an unrecoverable picker loop).

Instead:

* the model is given the prior plan with its ``[DONE]`` markers and is
  responsible for carrying completed steps forward and for setting
  ``current_step_index`` (0-based index of the first non-``done`` step, or the
  step count when all are done);
* the returned ``PlannerOutput`` is validated **structurally** by
  ``PlanSchema.validate_plan()`` (positive/unique step numbers, ``depends_on``
  resolving inside the plan, in-range ``current_step_index``); ``Planner._validate_result``
  surfaces any error;
* a validation failure triggers a bounded re-invoke through the generic
  ``AbstractLLMNode`` validation-retry loop (``max_validation_retries``;
  the error is exposed to the prompt as ``validation_feedback``), after which
  the invalid result is accepted fail-closed and ``_update_state`` routes it
  to ``unplannable``.

The general principle: machine-checkable consistency lives in schema
validation + fail-closed handling, not in prompt rules or positional patches.

### Planner policy on infeasible and read-only tasks

Two related rules keep the Planner from inventing work:

* **No write steps for read-only intent.**  A task whose intent is only to
  read, inspect, or report must not contain create/edit/delete steps.  If the
  requested artifact is absent, the plan surfaces the absence (report not
  found, or ask for the path) instead of fabricating it.  Observed failure:
  asked to read a missing file, an earlier planner added a step to create an
  empty file so the "read" would succeed.
* **Early exits have equal value.**  Identifying that a task is impossible or
  has a missing dependency is as valuable as completing it.  When early
  evidence shows the goal cannot be met, the Planner prefers reporting that
  over adding steps that cannot succeed.

## Plan review (human-in-the-loop)

``AwaitReview`` is human evaluation of the plan, symmetric to the Evaluator's
judgement of steps, but it carries **no LLM call**: it captures the user's
free-text input into ``human_feedback`` and routes back to the Planner.  The
Planner interprets the feedback: "good to go" -> ``in_progress``; changes ->
a revised plan with ``in_review`` (the review loop).  The Planner is the sole
plan/status writer, so ``in_review`` and the feedback cannot diverge from the
routing state.  The general path triggers review only when the Planner decides
it is needed; the scientific path may additionally require final-plan approval.

For the first implementation stage ``AwaitReview`` is a stub that supplies a
canned "looks good, proceed" input, exercising the loop without LangGraph
``interrupt``.  Real ``interrupt``/``Command(resume=...)`` (and the API/UI
resume path, including resume-of-same-execution vs new-turn semantics) is a
separate pending stage (HITL interrupt/resume ADR).  That stage must also wire
``needs_input``: today it terminates with the question in the answer, and the
next turn is a fresh run; the interrupt should resume the same run with the
answer and the plan/state intact.

## Tool disclosure

The same ``tools_info`` built by ``BaseLangGraph`` is disclosed per node:

| Node | Tool info | Rationale |
|------|-----------|-----------|
| RouteDecision | no tool list | narrow chat|task routing; tools would only tempt capability guessing |
| Planner | name + docstring, no parameter list | plans executable steps and suggests real tools |
| ToolsPicker | full description incl. parameter list | needs argument detail to emit ``ToolCallsSchema`` |

Plans contain **tool-executable steps** and **reasoning steps**, selected by
``StepSchema.kind``.  A self-contained request that needs no tools is answered
inline by ``RouteDecision`` (``chat``).  Tool identity is the Planner's: each
tool step names its tool(s) in ``suggested_tools`` (normally one; more only for
overlapping alternatives).  The picker **binds arguments only** and never
substitutes: it sees the full static catalogue (prompt-cache stability,
ADR-0028) but is instructed to use only the step's suggested tools.  When none
can carry out the step it returns a single empty-``tool`` call carrying the
reason; the picker node records that as a synthetic ``is_error`` observation
plus ``replan_reason``, and the graph escalates to the Planner.  The picker is
shared with RAG, which has no plan and selects freely from all tools.

Access level (ADR-0037): a ``read_only | full`` policy filters the Planner's
and picker's tool lists by the MCP ``read_only``/``destructive`` annotations and
is hard-enforced at dispatch, so a read-only run can still plan and inspect but
cannot mutate.

The Planner sees all configured tools (bundled plus domain).  Pruning or
filtering is deferred; if it becomes necessary, prefer multi-domain selection
(ADR-0011) or tool retrieval over single-domain classification.  Caching: the
tool block is kept stable so provider prefix caching (ADR-0028) still applies.

Planner and ToolsPicker stay separate nodes: the Planner runs once (or on
replan) without per-step outputs, while the picker runs after each batch with
the latest observations so it can bind arguments that depend on prior outputs
(ADR-0020/0035).

## State (relevant fields)

* ``route: RouteSchema`` (entry routing: ``chat`` | ``task``, with the inline
  chat answer).
* ``goal: GoalSchema`` (fixed task goal and success criteria; written only by
  the Planner, and only while unset).
* ``plan: PlanSchema`` (steps, per-step ``success_criteria``/``status``/
  ``kind``/``suggested_tools``, current step index, and the lifecycle ``status``
  used for routing).  The Planner authors all of it (Design A, above); code only
  validates structure.
* ``step_outputs: dict[int, list[StepOutput]]`` (per-step results; a
  ``StepOutput.result`` is either a ``CallToolResult`` or, for a reasoning
  step, a plain ``str`` conclusion).  Plan-scoped: cleared each turn and on a
  new plan.
* ``tool_calls`` / ``tool_results`` (current batch; shared with RAG).
* ``tool_retry_counts: dict[int, int]`` (consecutive tool-error batches per
  step; ADaPT re-pick budget).
* ``step_attempt_counts: dict[int, int]`` (non-advancing evaluations per step;
  semantic no-progress budget).
* ``plan_revisions: int`` (automated replans since the initial plan or the last
  human review; replan budget; review entries reset it).
* ``picker_attempts``/``picker_step`` (consecutive unusable picker selections
  and the step they belong to; bounded retry before escalating).
* ``replan_reason: str`` (unified reason the Planner is re-entered: set by the
  Evaluator on ``need_replan`` and by the tool-round recorder on a failed
  batch; read and cleared by the Planner).
* ``pending_question: str`` (the question to ask when ``plan.status`` is
  ``needs_input``).
* ``tool_rounds: int`` (ToolsPicker -> ToolsCaller dispatch rounds in the run;
  global backstop).
* ``failure_reason: str`` (why the run failed or could not be planned).
* ``human_feedback: str`` (latest review input; empty otherwise).
* ``evaluation: EvaluationSchema`` (latest operational verdict + reason).
* ``artefacts: dict[str, ArtefactSchema]`` (session-scoped durable results; the
  completed task's deliverable is persisted here, see Persistence below).
* ``messages`` (run history: query, plan/verdict progress, review input,
  final answer).
* ``mode: Mode`` (requested / resolved / note).

## Persistence (plan-scoped vs session-scoped)

State lifetime is split so a bounded working set carries across a run while
only curated results cross into later tasks:

* **Plan-scoped working memory** (`step_outputs`, the retry/attempt counters)
  is visible to later steps and cleared by ``InitGraphState`` each turn and by
  the Planner when it authors a new plan.
* **Session-scoped** (`artefacts`, plus ``messages`` for lossy continuity,
  ``mode`` and ``discovery_persistent``) survives across tasks in the same
  session.  ``AnswerFromResults`` auto-persists the completed task's
  deliverable as a concise ``ArtefactSchema`` (goal + result, keyed by a slug of
  the goal so a re-run supersedes); failure and ``needs_input`` persist nothing.
  The Planner sees the rendered artefacts (`artefacts_text()`), so a later task
  can build on an earlier one.  Intermediate tool/reasoning outputs never leak
  into artefacts.

## Failure signals (levels)

* Level 0, structural/deterministic: emission parse/validation failure; tool
  ``is_error``; runtime signal (exit code, not found, no matches); artifact
  predicate (file exists, schema validates); budget exhausted.
* Level 1, observable-against-criterion: the current step's
  ``success_criteria`` is checked by the evaluator, mechanically where
  possible.
* Level 2, independent LLM verification: a separate evaluator node (never
  same-call self-critique); ideally a different model when configured.
* Level 3, human: ambiguity -> ask; consequential actions -> approval
  (``AwaitReview``).

General mode prioritises level 0; level 2 is operational and results are
unverified.  Scientific mode requires level 1 criteria and an independent
level 2 verifier with provenance.

## Failure triage

| Cause | Route | Notes |
|-------|-------|-------|
| transient (network, timeout, 5xx) | retry at dispatch | bounded (ADR-0017 handles LLM; dispatch handles tools) |
| call-level (bad args, permission denied) | picker | re-pick the **same** tool with corrected arguments; the picker may not switch tools |
| picker cannot bind any suggested tool | planner | a single empty-``tool`` call becomes a synthetic ``is_error`` observation + ``replan_reason``; the picker does not substitute |
| repeated tool error on the same step | planner | ADaPT: ``tool_retry_counts`` N re-picks, then escalate |
| step makes no progress (criterion unmet, no new information) | planner | ``step_attempt_counts`` cap, then escalate |
| goal proven unreachable (missing input, read-only) | failure answer | Evaluator ``abort`` (no replan) |
| plan cannot be revised usefully | failure answer | ``plan_revisions``/``tool_rounds`` cap -> ``abort`` |

Failure is attributed per call, not per round: successful calls in a round are
kept and failed calls are re-picked.  Once a call succeeds, the step's earlier
errored outputs are pruned from the observations (they remain in ``messages``
and the node stream), so superseded failures do not mislead the Evaluator or
the answer synthesis.

## Goal handling

The Planner is the sole writer of ``state.goal`` (the goal plus task-level
success criteria).  ``_update_state`` writes it only while it is unset, so a
replan or a review revision within the same execution cannot move the success
reference.  Immutability is per execution, not per session:
``InitGraphState`` resets ``goal`` (and ``plan``) at the start of every turn,
so a new request gets a fresh goal without a new session.

## Evaluator verdicts and routing

The Evaluator judges only and runs after every tool round.  Its
verdict is a pydantic model whose next-step is a ``Literal``:

* ``step_incomplete`` -> step entry (more calls for the current step)
* ``step_done`` -> step entry (next step) if steps remain
* ``plan_done`` -> AnswerFromResults -> AnswerUser
* ``need_replan`` -> Planner
* ``abort`` -> AnswerFromResults (failure)

It judges the goal and the current step's criterion, not merely whether tools
succeeded; it is given the executed tool names and judges the **outcome**, so a
different tool that meets the criteria is fine, while a material mismatch is
reported in ``reason``.  It never writes ``message_for_user``.  A final-step
``step_done`` is coerced to ``plan_done`` so the graph does not route back to
the picker past the end of the plan.  Scientific mode uses a separate,
independent, epistemic verifier with the same judge-only contract.

### Reachability and ``abort``

``abort`` is a first-class verdict, not only the tool-round backstop.  When the
observations already prove the goal is unreachable -- for example a required
input does not exist and the task is read-only, so it must not be created -- the
Evaluator returns ``abort`` directly, routing straight to ``AnswerFromResults``
rather than re-entering the Planner.  Requiring ``need_replan`` for an
unreachable goal burned the whole replan budget re-planning a dead end (observed:
five identical replans before failing).

Reachability is a *judgement against evidence*, which the Evaluator already
makes; it is deliberately **not** implemented as a plan-similarity check.  There
is no deterministic way to tell whether two plans are "the same" (wording, step
count and tool choice vary between calls), so no such heuristic is used.  The
Evaluator's ``abort`` reason is carried into ``failure_reason`` so the failure
answer explains the real cause; only a budget-triggered abort is labelled as
such in ``OperationalEvaluator._update_state``.

## Termination and budgets

The deterministic guards bound every loop and mirror RAG's ``RouteEvaluator``:
counters live in state (incremented by the acting nodes) and the caps are
enforced deterministically in the Evaluator/Planner ``_update_state`` (not by
the LLM).  ``tool_retry_counts`` (triage), ``step_attempt_counts`` and
``plan_revisions`` escalate to a replan/``unplannable``; ``tool_rounds`` sets
``abort`` and the failure answer, so the act/eval loop always terminates.  The
Evaluator prompt uses ``step_incomplete`` only when a specific further call is
expected, ``need_replan`` when the observations show no progress toward the
criterion, and ``abort`` when they show the goal is unreachable (above).

## Answer synthesis

``AnswerFromResults`` runs once when the outcome is ``plan_done`` (success),
``abort``/``unplannable`` (failure), or ``needs_input`` (ask the pending
question).  On success it writes ``message_for_user`` from the goal, the
completed plan and the observations, and persists the deliverable to
``artefacts`` (see Persistence); on failure it explains concisely what was
attempted and why it could not be completed (using ``failure_reason``, the plan
and the observations); on ``needs_input`` it asks ``pending_question``.  The
outcome-specific detail (failure reason or question) is rendered as a single
conditional block, omitted entirely on success.  It judges nothing.  Its prompt
carries the output-formatting rules (verbatim command/list output in fenced
code blocks, identifiers in backticks, a blank line before lists, no raw JSON
dumps).  A deterministic fallback covers an empty or failed synthesis.
Scientific mode will use a grounded, citation-carrying variant; the Evaluator
contract is unchanged.

## Escalation

* Structural escalation (default): same model, more process -- planning,
  decomposition, retrieval, retries.  Always available.
* Model escalation (optional): requires more than one configured model.  Not
  implemented as an automatic cascade; model choice is a static per-role
  assignment.
* Single-model default: every role may use the one configured model; the
  topology is fully functional single-model.

## Mode boundary

The general path contains no mandatory
retrieval/evidence/gating/verification/provenance stages (ADR-0036).
Retrieval is an optional ``search_stores``-style tool.  Scientific mode inserts
the ADR-0029 layer additively.

## Relationship to shared nodes

``ToolsPicker``/``ToolsCallerNode`` are shared with RAG (ADR-0020).  The
evaluator and planner are agent nodes; the general evaluator contract
(operational) differs from the scientific evaluator contract (epistemic), even
if code is shared.
