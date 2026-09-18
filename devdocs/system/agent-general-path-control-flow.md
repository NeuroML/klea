# General-path control flow

Status: design note, not an ADR.  Concrete mechanics for ADR-0035; to be
replaced by the C4 agent component diagram once the graph is implemented.
Written 2026-09-10 by opencode (model: deepseek-flash); revised same day to
the narrow-router + task-path topology.

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
                 '-- status in_progress ---> step entry

Step entry (the tools picker is the sole tool selector):
  ToolsPicker --empty selection--> Planner   (no suitable tool: replan)
              --otherwise--------> ToolsCaller -> TriageRouter

TriageRouter (deterministic, tool-error triage):
  error, retries left  -> ToolsPicker
  retries exhausted    -> Planner
  no error             -> Evaluator

Evaluator (operational judge):
  step_incomplete -> step entry
  step_done       -> step entry (next step)
  need_replan     -> Planner
  plan_done       -> AnswerFromResults -> AnswerUser -> END
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
| ToolsPicker + ToolsCaller (Act) | emit ``ToolCallsSchema`` and dispatch parallel tool calls (ADR-0020/0034) | picker yes, caller no |
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

The Planner (task path only) emits ``PlannerOutput {goal, plan}``;
``plan.status`` is the post-Planner routing source:

| status | meaning | edge |
|--------|---------|------|
| ``in_review`` | plan produced, awaiting human review | ``AwaitReview`` |
| ``unplannable`` | no viable plan | ``AnswerFromResults`` (failure) |
| ``in_progress`` | ready; execute | step entry |

``Planner._update_state``: steps + review -> ``in_review``; steps ->
``in_progress``; no steps -> ``unplannable``.  It never answers the user.

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
separate stage recorded as ADR-0037.

## Tool disclosure

The same ``tools_info`` built by ``BaseLangGraph`` is disclosed per node:

| Node | Tool info | Rationale |
|------|-----------|-----------|
| RouteDecision | no tool list | narrow chat|task routing; tools would only tempt capability guessing |
| Planner | name + docstring, no parameter list | plans executable steps and suggests real tools |
| ToolsPicker | full description incl. parameter list | needs argument detail to emit ``ToolCallsSchema`` |

Plans are **tool-executable only**: a self-contained request that needs no tools
is answered inline by ``RouteDecision`` (``chat``), so a plan step always goes
through the picker.  The Planner's per-step ``suggested_tools`` is a **prior**,
not a binding: the picker honours it when it fits and otherwise picks a
different available tool (giving a ``reason``), or returns an empty list when
nothing fits (-> replan).  The outcome contract is the step's success criteria,
judged by the Evaluator; a different tool that meets the criteria is fine, and a
tool that does not is caught by ``need_replan``.  The picker is shared with RAG,
which has no suggestions and simply selects from all tools.

Access level (deferred; its own ADR): a ``read_only | full`` policy that
filters the Planner's and picker's tool lists by the MCP
``read_only``/``destructive`` annotations and is hard-enforced at dispatch, so
a read-only run can still plan and inspect but cannot mutate.

The Planner sees all configured tools (bundled plus domain).  Pruning or
filtering is deferred; if it becomes necessary, prefer multi-domain selection
(ADR-0011) or tool retrieval over single-domain classification.  Caching: v1
keeps the tool block stable so provider prefix caching (ADR-0028) still
applies.

Planner and ToolsPicker stay separate nodes: the picker runs after each batch
with the latest results so it can set arguments that depend on prior outputs,
and it is shared with RAG (ADR-0020).

## State (relevant fields)

* ``route: RouteSchema`` (entry routing: ``chat`` | ``task``, with the inline
  chat answer).
* ``goal: GoalSchema`` (fixed task goal and success criteria; written only by
  the Planner, and only while unset).
* ``plan: PlanSchema`` (steps, per-step ``success_criteria``/``status``,
  current step index, and the lifecycle ``status`` used for routing).  The
  Planner authors all of it (Design A, above); code only validates structure.
* ``step_outputs: dict[int, list[CallToolResult]]`` (per-step results).
* ``tool_calls`` / ``tool_results`` (current batch; shared with RAG).
* ``tool_retry_counts: dict[int, int]`` (consecutive tool-error batches per
  step; ADaPT re-pick budget).
* ``step_attempt_counts: dict[int, int]`` (non-advancing evaluations per step;
  semantic no-progress budget).
* ``plan_revisions: int`` (Planner entries; replan budget).
* ``tool_rounds: int`` (ToolsPicker -> ToolsCaller dispatch rounds in the run;
  global backstop).
* ``failure_reason: str`` (why the run failed or could not be planned).
* ``human_feedback: str`` (latest review input; empty otherwise).
* ``evaluation: EvaluationSchema`` (latest operational verdict + reason).
* ``messages`` (run history: query, plan/verdict progress, review input,
  final answer).
* ``mode: Mode`` (requested / resolved / note).

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
| call-level (bad args, wrong tool, permission denied) | picker | re-pick with the error fed back |
| repeated tool error on the same step | planner | ADaPT: ``tool_retry_counts`` N re-picks, then escalate |
| step makes no progress (criterion unmet, no new information) | planner | ``step_attempt_counts`` cap, then escalate |
| goal proven unreachable (missing input, read-only) | failure answer | Evaluator ``abort`` (no replan) |
| plan cannot be revised usefully | failure answer | ``plan_revisions``/``tool_rounds`` cap -> ``abort`` |

Failure is attributed per call, not per round: successful calls in a round are
kept, failed calls are re-picked.

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

``AnswerFromResults`` runs once when the outcome is ``plan_done`` (success) or
``abort``/``unplannable`` (failure).  On success it writes ``message_for_user``
from the goal, the completed plan and the observations; on failure it explains
concisely what was attempted and why it could not be completed (using
``failure_reason``, the plan and the observations).  It judges nothing.  Its
prompt carries the output-formatting rules (verbatim command/list output in
fenced code blocks, identifiers in backticks, a blank line before lists, no raw
JSON dumps).  A deterministic fallback covers an empty or failed synthesis.
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
