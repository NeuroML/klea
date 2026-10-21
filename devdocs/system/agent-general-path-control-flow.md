# General-path control flow

Status: design note, not an ADR.  Concrete mechanics for ADR-0035; the C4
Level 3 view of the same graph is `c4-component-agent.md`.
Written 2026-09-10 by opencode (model: deepseek-flash); revised same day to
the narrow-router + task-path topology; revised 2026-09-19 for reasoning
steps, the tool-identity contract, the unified replan reason, ``needs_input``
and session-scoped artefacts (ADR-0035 update 2026-09-19); revised 2026-09-21
for the ADR-0041 dependency-frontier model: per-step statuses replace the
``current_step_index`` cursor, work is dispatched in same-kind frontier
batches, the Evaluator returns per-step verdicts, and the caller serialises
same-resource calls; revised 2026-10-20 for failed-batch feedback and the
cross-batch repeated-call guard.

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
                 |-- status chat ---------> RouteDecision (forced chat) -> AnswerUser -> END
                 |-- status completed -----> AnswerFromResults (all steps already done) -> AnswerUser -> END
                 |-- status needs_input ---> AwaitHuman (input) --answer--> Planner
                 '-- status in_progress ---> next step by kind (ADR-0041):
                       review -------> AwaitHuman (review) --approve--> next step
                       |                                 '--revise----> Planner
                       tool ---------> batch entry (tool steps)
                       reasoning ----> ReasoningNode -> Evaluator

Batch entry (maximal same-kind prefix of the frontier, capped at 8 steps):
  kind = tool:
    ToolCallBinder (binds one contiguous group of steps with the same
      suggested-tool set; only those tools are disclosed)
      --usable call(s)------> more groups? -> ToolCallBinder (bind_next)
                              all bound  -> ToolsCaller -> TriageRouter
      --deliberate failure--> Planner   (synthetic error observation + replan_reason)
      --empty (glitch)------> ToolCallBinder (bounded retry), then Planner
  kind = reasoning:
    ReasoningNode (one reasoning step per round) -> Evaluator

ToolsCaller (ADR-0041): receives the accumulated calls, groups them by resource
  (``checkpaths``) and runs same-resource calls sequentially in step order; the
  rest run concurrently.

TriageRouter (deterministic, per-step tool-error triage):
  every errored step within budget -> ToolCallBinder
  any errored step over budget     -> Planner
  no error                         -> Evaluator

Evaluator (operational judge, per-step verdict map):
  each step_done / step_skipped / step_incomplete -> batch entry (updated frontier, by kind)
  any step need_replan                            -> Planner
  overall plan_done with no pending step          -> AnswerFromResults (persists the deliverable) -> AnswerUser -> END
  overall abort                                   -> AnswerFromResults (failure) -> AnswerUser -> END
  inconsistent (plan_done + pending, or all resolved + no overall) -> one retry, then fail closed (Planner / answer)
```

Trivial chat: Guard + RouteDecision (2 calls); the router answers inline and the
graph goes straight to ``AnswerUser``.  ``RouteDecision`` is deliberately
narrow and tool-free -- it decides "self-contained conversational/general-knowledge
reply vs needs the environment **or derives from earlier session work**",
defaulting to ``task`` (fail-closed) so a world-fact is never answered from
assumption and a derivation is never answered inline from the conversation.
Routing a derivation to ``task`` is cheap when no environment work is needed:
the Planner plans a single ``reasoning`` step that synthesises the already-known
facts, and the answer-composer writes (and persists) the result from the
resulting observation.  There is no zero-step "already known" bypass.  If the
Planner instead finds that the request was a conversational turn (no task at
all), it returns ``chat`` and the run goes back to ``RouteDecision``, which is
forced to answer as chat from conversation history; the answer-composer is
deliberately bypassed because it has no memory.
On that forced pass the router's result is validated (a ``chat`` route with a
non-empty answer), so an ignored override triggers a bounded re-invoke; the
graph routing itself is still forced to ``chat`` regardless of the model's
route field.

## Nodes

Node field definitions, structured-output schemas, and state-update contracts
are maintained in [Agent node contracts](agent-node-contracts.md). This document
remains the source for workflow topology and failure policy.

| Node | Role | LLM |
|------|------|-----|
| InitGraphState | seed/reset state | no |
| ModeDecision / ModeInformer | resolve mode (ADR-0030) | no |
| Guard | safety classification (ADR-0010) | yes (guard role) |
| RouteDecision | narrow entry router: ``chat`` (answer inline) vs ``task`` (fail-closed) | yes (chat role) |
| Planner | task-path brain: write the immutable goal, create/revise the plan (ADR-0035); never answers the user | yes (plan role) |
| ReviewProgress | synthesise a human-facing digest of the work since the last review, immediately before a review gate; calls no tools (ADR-0051) | yes (chat role) |
| AwaitHuman | HITL pause/resume (ADR-0046/ADR-0051): a review step (approve/revise) or a blocked step's questions; no LLM | no |
| ToolCallBinder + ToolsCaller (Act, ``kind=tool``) | bind arguments for one contiguous group of steps sharing a suggested-tool set; accumulate calls and dispatch the bound batch, serialising same-resource calls (ADR-0020/0034/0041); never substitute a different tool | binder yes, caller no |
| ReasoningNode (``kind=reasoning``) | produce one step's conclusion from the goal/plan/observations; records a ``str`` step output; no binder/caller (serial; batching deferred, ADR-0041) | yes (chat role) |
| TriageRouter | deterministic per-step tool-error triage: which steps errored? retries left? | no |
| Evaluator | operational judge only: goal + each batch step's criterion; per-step verdict map plus an overall outcome; also checks review readiness when a gate is next (ADR-0051) | yes (chat role; separate node from Act) |
| AnswerFromResults | synthesise the user answer (success) or the failure explanation | yes (chat role) |
| AnswerUser | deliver the final user-facing message | no |

## Entry routing and Planner outcomes

``RouteDecision`` emits a ``RouteSchema`` and is the only place a model may
answer the user directly:

| route | meaning | edge |
|-------|---------|------|
| ``chat`` | self-contained conversational/general-knowledge reply (greeting, clarification, general fact, direct conversational edit); the router wrote ``answer``.  Does not derive from session work | ``AnswerUser`` |
| ``task`` | needs the environment/workspace/session, **or** derives a result from earlier work/turns (summarise, compile, compare, synthesise) | ``Planner`` |

The Planner (task path only) emits ``PlannerOutput {goal, plan, reason}``;
``plan.status`` is the post-Planner routing source:

| status | meaning | edge |
|--------|---------|------|
| ``needs_input`` | a step carries ``needs_input`` questions, or the replan budget is exhausted and the Planner appends a guidance step: a draft the Planner re-authors after the answers | ``AwaitHuman`` (input) |
| ``chat`` | the entry router misclassified a conversational request as ``task``; return no steps and no goal | ``RouteDecision`` (forced chat) -> ``AnswerUser`` |
| ``unplannable`` | no viable plan; the last authored steps are preserved for the status pane | ``AnswerFromResults`` (failure) |
| ``in_progress`` | ready; dispatch the next step by ``kind``: a ``review`` step pauses for human review, ``tool``/``reasoning`` execute | ``ReviewProgress`` -> ``AwaitHuman`` (review) or batch entry |

``Planner._update_state``: any step with ``needs_input`` questions forces
``needs_input`` (a draft; the status is derived from the steps); steps ->
``in_progress`` (a ``review`` step pauses when it reaches the frontier; there is
no review status -- ADR-0051); ``chat`` writes no goal and no steps and hands
the run back to ``RouteDecision``, which is forced to answer as chat from
conversation history (the request is not a task); ``unplannable`` (Planner
decided no viable plan, or validation retries exhausted) keeps the last
authored steps for the status pane and routes to the failure answer.  On
deterministic replan-budget exhaustion the Planner preserves the steps, appends
a blocked "Provide guidance to continue" step, and writes ``needs_input`` so
the input HITL asks the user how to proceed.  An ``in_progress`` plan must carry
at least one step: a task whose facts are already known is planned as a
``reasoning`` step, so the conclusion reaches the answer-composer through
observations.  The Planner never answers the user.
``PlannerPlanSchema`` exposes only
these editable statuses to the model; the state ``PlanSchema`` also carries the
runtime ones
(``not_started``/``completed``/``failed``/``aborted``/``user_cancelled``)
written by code.

### Planner plan ownership (Design A)

The Planner is the **sole author of the whole plan**, including each step's
``status`` and ``depends_on``.  Code does **not** mutate the plan: there is no
re-application of completion markers and no recomputation of positions by
matching step numbers across plans.  That positional matching was brittle --
on a replan the model may renumber or merge steps, so an old ``done`` step
``1`` could collide with a new step ``1`` and be wrongly forced ``done``
(observed: an unrecoverable picker loop).

Instead:

* the model is given the prior plan with its ``[DONE]`` markers and is
  responsible for carrying completed steps forward and for authoring
  ``depends_on`` (the earlier steps each step needs);
* the returned ``PlannerOutput`` is validated **structurally** by
  ``PlanSchema.validate_plan()`` (positive/unique step numbers,
  ``depends_on`` referencing only in-plan and strictly-earlier steps, an
  acyclic graph via ``graphlib``); ``Planner._validate_result`` surfaces any
  error;
* every ``tool`` step names only tools disclosed at the run's
  ``access_level``; a tool step is rejected when the catalogue is empty
  (nothing to bind or dispatch) or when a named tool is unknown or filtered
  out by ``read_only``;
* a validation failure triggers a bounded re-invoke through the generic
  ``AbstractLLMNode`` validation-retry loop (``max_validation_retries``;
  the error is exposed to the prompt as ``validation_feedback``).  The loop
  clears the feedback when a result finally validates; if the retries are
  exhausted it leaves the last error in ``ctx.validation_feedback`` so
  ``_update_state`` can fail closed -- the Planner writes ``unplannable``
  with the error in ``failure_reason`` instead of accepting the invalid
  plan.

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

## Plan review and step questions (human-in-the-loop)

Human-in-the-loop is a single pause/resume mechanism (ADR-0046).  A reusable
template node (``klea_utils/nodes/await_human.py``, ``AwaitHumanNode``) owns
the LangGraph ``interrupt``/``Command(resume=...)`` mechanism; the agent policy
(``AwaitHuman``) is instantiated twice.

Both review entry points pass through ``ReviewProgress`` first: a small
``chat`` node (no tools) that synthesizes the work completed since the last
review into a human-facing digest, presented as the ask's ``Progress`` section.
It is scoped by the highest approved gate (``PlanSchema.reviewed_through``) and
skipped when there is no new work, so a whole-plan approval carries no digest.
Any evidence the reviewer needs is planned by the Planner as ordinary tool
steps before the gate; ``ReviewProgress`` only summarizes the recorded
observations, so it covers arbitrary tools, not just file edits.  When the next
runnable batch is a gate, the Evaluator also judges *review readiness*: if a
concrete evidence step is missing it replans to add it (``not_ready``), rather
than firing a gate the user cannot decide on.  That replan consumes the
Planner's automated-replan budget; at the boundary the Evaluator stops
replanning and lets the gate fire, so a readiness issue never fails the run.

* ``AwaitHuman`` **review** pauses at a ``review`` step (ADR-0051): human
  evaluation of the plan or the progress so far, symmetric to the Evaluator's
  judgement of steps, with **no LLM call**.  It captures an explicit decision
  (``ReviewResponse``: approve / revise / cancel).  A ``review`` step is a
  **pure gate**: it names no tools and carries no action, so any evidence the
  user needs (a diff, a test run, a rendered report) is a separate ``tool``
  step immediately before the gate (see above).  A ``review`` step with no
  ``depends_on`` is a whole-plan approval; one after step N (``depends_on: [N]``)
  is a checkpoint.  A review only approves **pending** work, so its ask carries
  the pending steps it unblocks -- the whole plan (approval) or the remaining
  steps (checkpoint).  Every pending step is listed, including the review gates
  (marked as human review), so the plan is shown complete; the headline counts
  the pending work steps only, because the gate is the approval action itself.
  A checkpoint may also carry a `Progress` section: a synthesized digest of the
  work completed since the last review (the highest approved gate scopes it), so
  the user can decide without reading the whole transcript.  Completed steps and
  their findings are not otherwise shown (the findings are in the transcript and
  the inspection pane).  A review step must
  gate at least one pending step after it; a *trailing* review (none after it)
  is rejected by the Planner's validator and resolved defensively by the router.
  **Approve** deterministically marks the review step(s) at the
  frontier ``done`` and the router sends the run straight on (no Planner call);
  **revise** returns the feedback to the Planner, which owns the revised plan;
  **cancel** ends the run (``user_cancelled``).
* ``AwaitHuman`` **input** asks the questions a *blocked step* carries
  (``StepSchema.needs_input``); the answers resume the same run at the
  Planner, which re-authors the plan without the questions.

A step with ``needs_input`` questions is a draft: it is not executed (and is
exempt from the executable-step checks) until the Planner re-authors it.  The
plan status ``needs_input`` is derived from the steps, so a plan can never be
"runnable" while carrying unanswered questions.  A plan-level question is just
a clarification step carrying its ``needs_input``.  ``needs_input`` supplies
facts only -- never approval; there is no bound on the number of input rounds.

The resume carries an optional typed ``response_schema`` (forwarded on the
stream event as ``hitl_response_schema``).  A plain query (or a failure resume)
while a thread is paused is rejected with ``409`` in the backend, so an
interrupt is never silently ignored.  See ``devdocs/system/graph-resume.md``
for the confirmed pause/resume semantics.

## Tool disclosure

The same ``tools_info`` built by ``BaseLangGraph`` is disclosed per node:

| Node | Tool info | Rationale |
|------|-----------|-----------|
| RouteDecision | no tool list | narrow chat|task routing; tools would only tempt capability guessing |
| Planner | name + docstring, no parameter list | plans executable steps and suggests real tools |
| ToolCallBinder | full description incl. parameter list | needs argument detail to emit ``ToolCallsSchema`` for the group's suggested tools |

Plans contain **tool-executable steps** and **reasoning steps**, selected by
``StepSchema.kind``.  A ``reasoning`` step derives an intermediate conclusion a
later step consumes; it is neither evaluation (the Evaluator judges each step's
``success_criteria``) nor the final answer (``AnswerFromResults`` writes the
reply, including the final synthesis, and persists the result), so the Planner
must not plan either as a step
(``Planner_system.md``).  A self-contained request that needs no tools is answered
inline by ``RouteDecision`` (``chat``).  Tool identity is the Planner's: each
tool step names its tool(s) in ``suggested_tools`` (normally one; more only for
overlapping alternatives).  The **ToolCallBinder** binds arguments only and
never substitutes.  Each invocation covers one contiguous group of batch steps
whose ``suggested_tools`` are an identical set (capped at 4 steps), and exposes
only that set: the binder cannot choose an unplanned tool.  If a group cannot
be bound the binder returns a single empty-``tool`` call carrying the reason;
the node records that as a synthetic ``is_error`` observation plus
``replan_reason``, and the graph escalates to the Planner.  A call that repeats
a previously failed ``(tool, arguments)`` pair is also a deliberate failure,
even when it appears in a subset, a reordered batch, or alongside new calls, so
the binder cannot spend a round on an identical retry.  Bound calls accumulate
in ``binding_calls`` until the whole batch is bound; the caller then dispatches
them, still in parallel.  RAG keeps the shared ``ToolsPicker``, which has no
plan and selects freely from its domain tools.

Access level (ADR-0037): a ``read_only | full`` policy filters the Planner's
and binder's tool lists by the MCP ``read_only``/``destructive`` annotations and
is hard-enforced at dispatch, so a read-only run can still plan and inspect but
cannot mutate.

The Planner sees all configured tools (bundled plus domain).  Pruning or
filtering is deferred; if it becomes necessary, prefer multi-domain selection
(ADR-0011) or tool retrieval over single-domain classification.  Caching: the
tool block is kept stable so provider prefix caching (ADR-0028) still applies.

Planner and ToolCallBinder stay separate nodes: the Planner runs once (or on
replan) without per-step outputs, while the binder runs after each group with
the latest observations so it can bind arguments that depend on prior outputs
(ADR-0020/0035).

## Parallel execution (ADR-0041)

The plan is a directed acyclic graph: ``StepSchema.depends_on`` names the
earlier steps a step needs.  Execution position is **derived, not stored**:

* **Frontier** -- pending steps whose ``depends_on`` are all ``done`` or
  ``skipped``, in step order.  ``depends_on: []`` is immediately runnable.
* **Batch** -- the maximal *same-kind* prefix of the frontier (capped at 8
  steps), so tool steps go through the binder/caller and reasoning steps
  through the reasoning node.  A mixed frontier is drained one kind at a time.
* **Binder binding** -- one invocation binds one contiguous group of steps
  sharing a suggested-tool set (capped at 4 steps); calls are tagged with their
  originating ``step`` and accumulate until the batch is bound (capped at 16
  calls).  The caller then dispatches the whole batch in parallel.
* **Resource guard** -- the caller groups the batch's calls by resource (the
  tool's ``checkpaths`` arguments, compared lexically with ``os.path.normpath``)
  and runs same-resource calls sequentially in step order, the rest
  concurrently.  A missed ``depends_on`` edge therefore costs parallelism
  rather than a lost update; calls with no identifiable resource run
  concurrently.
* **Evaluator** -- judges the whole batch and returns a per-step verdict map.

Reasoning steps remain serial (one conclusion per round); batching them is a
deferred ADR-0041 open item.

## State (relevant fields)

* ``route: RouteSchema`` (entry routing: ``chat`` | ``task``, with the inline
  chat answer).
* ``goal: GoalSchema`` (fixed task goal and success criteria; written only by
  the Planner, and only while unset).
* ``plan: PlanSchema`` (steps with their ``success_criteria``/``status``/
  ``kind``/``suggested_tools``/``depends_on``, and the lifecycle ``status``
  used for routing).  The Planner authors all of it (Design A, above); code only
  validates structure.  Execution position is **derived**, not stored: the
  frontier and the next batch are computed from per-step statuses and
  ``depends_on`` (ADR-0041).  ``PlanSchema`` extends the authored
  ``PlannerPlanSchema`` and also carries the run-history counters:
  ``plan_version`` (monotonic; 0 = no plan, first authored plan = 1),
  ``human_feedback_rounds`` (human review rounds processed), and
  ``automated_plan_revisions`` (consecutive automated replans since the initial
  plan or last review; the budget counter, reset on review).  These counters
  are a context-free signal for the answer synthesis (ADR-0035 update
  2026-09-21).
* ``plan_history: list[PlanRevision]`` (superseded plan versions, newest last,
  capped at 9; each carries the full steps and the reason it was abandoned.
  The active plan remains in ``plan``, so the Planner and guidance HITL see
  1 active + 9 previous versions.  Reset per turn; retained across automated
  replans, human input and review revisions.  The cap is independent of the
  automated-replan budget, which resets on human input.)
* ``step_outputs: dict[int, list[StepOutput]]`` (per-step results; a
  ``StepOutput.result`` is either a ``CallToolResult`` or, for a reasoning
  step, a plain ``str`` conclusion).  Plan-scoped: cleared each turn and on a
  new plan.
* ``tool_calls`` / ``tool_results`` (current batch; shared with RAG).
* ``tool_retry_counts: dict[int, int]`` (consecutive tool-error batches per
  step; ADaPT re-pick budget).
* ``step_attempt_counts: dict[int, int]`` (non-advancing evaluations per step;
  semantic no-progress budget).
* ``picker_attempts``/``picker_step`` (consecutive unusable binder selections
  and the step they belong to; bounded retry before escalating).  The field
  names predate the binder rename.
* ``binding_calls: list[ToolCallSchema]`` (calls bound so far in the current
  batch; accumulated one tool-set group at a time and cleared after dispatch).
* ``failure_reason: str`` (why the run failed or could not be planned).
* ``evaluation: EvaluationSchema`` (latest per-step verdict map plus the
  ``overall`` outcome and a reason; ADR-0041).  ``step_incomplete`` reasons are
  read by the re-running actor as ``step_feedback``; the Planner clears the
  snapshot when it writes a new plan, so it cannot leak into the next one.
* ``artefacts: dict[str, ArtefactSchema]`` (session-scoped durable results; the
  completed task's deliverable is persisted here, see Persistence below).
* ``messages`` (user-visible conversation: query, HITL notes, final answer).
* ``messages_internal`` (internal run history: plan versions, evaluator
  verdicts and reasoning conclusions; checkpointed for provenance but never
  injected verbatim into a prompt).
* ``summarised_till`` / ``summarised_internal_till`` (per-stream summary
  cursors).
* ``mode: Mode`` (requested / resolved / note).

**Transient node-to-node signals** (not durable state; consumed/cleared within
the run; not rendered into downstream prompts):

* ``human_feedback: str`` -- the latest review input, written by ``AwaitHuman``
  (review) and interpreted then cleared by the Planner (which records the round
  on ``plan.human_feedback_rounds``).
* ``human_input: dict[int, list[str]]`` -- the answers to a blocked step's
  questions, written by ``AwaitHuman`` (input) and read/cleared by the Planner.
* ``replan_reason: str`` -- the unified automated-replan trigger, set by the
  Evaluator on ``need_replan`` and by the tool-round recorder from the failed
  batch digest (every failed call with its tool, path/URI arguments and error);
  read by the Planner, recorded on the superseded plan's ``plan_history``
  entry, then cleared.
* ``review_progress: str`` -- the synthesized digest of work since the last
  review, written by ``ReviewProgress`` and read by the review ask; cleared when
  a review is resolved and on a new plan.

## Persistence (plan-scoped vs session-scoped)

State lifetime is split so a bounded working set carries across a run while
only curated results cross into later tasks:

* **Plan-scoped working memory** (`step_outputs`, the retry/attempt counters)
  is visible to later steps and cleared by ``InitGraphState`` each turn and by
  the Planner when it authors a new plan.
* **Run-scoped plan history** (`plan_history`) is reset by ``InitGraphState``
  each turn but retained across plans, human input and review revisions within
  the run; the Planner does not clear it when authoring a new plan.
* **Session-scoped** (`artefacts`, plus ``messages``/``messages_internal``
  for continuity and provenance, ``mode`` and ``discovery_persistent``)
  survives across tasks in the same session.  ``InitGraphState`` refreshes
  ``discovery_persistent`` with the
  project instruction file (``AGENTS.md``, falling back to ``CLAUDE.md``),
  re-read only when the file changes; it is rendered into the Planner and
  ``AnswerFromResults`` prompts so project conventions reach both planning and
  the final reply.  ``AnswerFromResults`` auto-persists the completed task's
  deliverable as a concise ``ArtefactSchema`` (goal + result, keyed by a slug of
  the   goal so a re-run supersedes); failure persists nothing.
  Only ``AnswerFromResults`` sees the rendered artefacts (``artefacts_text()``),
  so the final reply can draw on prior deliverables without relying on lossy
  conversation memory.  The Planner does not receive artefacts: it reads the
  conversation history, and an already-known result is planned as a
  ``reasoning`` step whose conclusion enters observations.  Intermediate
  tool/reasoning outputs never leak into artefacts.  The rendered block is
  bounded (the most recently written 20 entries and 12,000 characters, with a
  note when older entries are omitted); the checkpoint still retains all
  artefacts.

## Failure signals (levels)

* Level 0, structural/deterministic: emission parse/validation failure; tool
  ``is_error``; runtime signal (exit code, not found, no matches); artifact
  predicate (file exists, schema validates); budget exhausted.
* Level 1, observable-against-criterion: the current step's
  ``success_criteria`` is checked by the evaluator, mechanically where
  possible.
* Level 2, independent LLM verification: a separate evaluator node (never
  same-call self-critique); ideally a different model when configured.
* Level 3, human: ambiguity -> ask (``AwaitHuman`` input); consequential
  actions -> approval (``AwaitHuman`` review).

General mode prioritises level 0; level 2 is operational and results are
unverified.  Scientific mode requires level 1 criteria and an independent
level 2 verifier with provenance.

## Failure triage

| Cause | Route | Notes |
|-------|-------|-------|
| transient (network, timeout, 5xx) | retry at dispatch | bounded (ADR-0017 handles LLM; dispatch handles tools) |
| call-level (bad args, permission denied) | binder | re-bind the **same** suggested tool with corrected arguments; the binder may not switch tools and sees every failed call, not just the last error |
| binder cannot bind any suggested tool | planner | a single empty-``tool`` call becomes a synthetic ``is_error`` observation + ``replan_reason``; the binder does not substitute |
| repeated failed call (same tool + arguments) | planner | deterministic guard: escalate even for a subset/reordered batch; changed arguments are allowed |
| repeated tool error on the same step | planner | ADaPT: ``tool_retry_counts`` 3 re-picks, then escalate |
| step makes no progress (criterion unmet, no new information) | planner | ``step_attempt_counts`` cap, then escalate |
| goal proven unreachable (missing input, read-only) | failure answer | Evaluator ``abort`` (no replan) |
| plan revision budget exhausted | human guidance | preserve steps, append a blocked guidance step, set ``needs_input`` -> ``AwaitHuman`` (input) |
| long plan | run continues | no step-count cap; the per-step/replan budgets bound execution |

Failure is attributed per call, not per round: successful calls in a round are
kept and failed calls are re-picked.  Once a call succeeds, the step's earlier
errored outputs are pruned from the observations (they remain in
``messages_internal`` and the node stream), so superseded failures do not
mislead the Evaluator or the answer synthesis.

Failed-batch feedback uses ``klea_utils.tools.tool_call_errors``: one line per
failed call with its step/tool, path/URI-named arguments (plus the picker's
declared ``paths``) and error.  URI credentials/secrets are redacted and the
digest is bounded; free-text arguments (contents, edit strings, code, shell
commands) are omitted.  The binder's ``step_feedback``, the Planner's
``replan_reason``, and the Evaluator's ``recent_tool_failures`` block all use
this same digest.  The binder's block also carries the evaluator's
``step_incomplete`` reasons; RAG's picker keeps the equivalent
``picker_feedback`` variable.

The memory summariser consumes both streams: user-visible ``messages`` (minus
its bounded recent window) and ``messages_internal`` from its own cursor.  It
updates ``context_summary`` and both cursors.  Node prompts receive
``context_summary`` plus a bounded window of ``messages`` only; internal run
history is never injected verbatim.

## Goal handling

The Planner is the sole writer of ``state.goal`` (the goal plus task-level
success criteria).  ``_update_state`` writes it only while it is unset, so a
replan or a review revision within the same execution cannot move the success
reference.  Immutability is per execution, not per session:
``InitGraphState`` resets ``goal`` (and ``plan``) at the start of every turn,
so a new request gets a fresh goal without a new session.

## Evaluator verdicts and routing

The Evaluator judges only and runs after every tool round.  It judges the
**batch** -- the steps marked ``[CURRENT]`` in its prompt (the same-kind
frontier prefix the binder/caller or reasoning node just ran) -- and returns an
``EvaluationSchema``: a map ``{step_number: {verdict, reason}}`` plus an
optional ``overall`` outcome.

Per-step verdicts:

* ``step_done`` -> the step is marked ``done`` and the frontier advances;
* ``step_skipped`` -> the step is marked ``skipped`` (not needed) and the
  frontier advances; a skipped dependency satisfies its dependents;
* ``step_incomplete`` -> the step stays ``pending``; its attempt counter grows,
  and its reason is passed to the next binder/reasoning invocation as
  ``step_feedback`` so the re-run can address it;
* ``need_replan`` -> the step is marked ``failed`` and its reason becomes the
  Planner's ``replan_reason``.

Overall outcome (when the plan is finished or unreachable):

* ``plan_done`` -> the goal is met.  Valid only when no step is ``pending``; a
  step that is not needed must be judged ``step_skipped`` in the same response.
  On success the plan is ``completed`` -> AnswerFromResults -> AnswerUser;
* ``abort`` -> the plan is ``aborted`` -> AnswerFromResults (failure).

Consistency (goal-authoritative, ADR-0035): the Evaluator judges the goal and
the steps, and the two must agree.  ``plan_done`` with a pending step, or an
all-resolved plan with no overall, is rejected and retried once with the reason
exposed as ``validation_feedback``.  If the model still returns an inconsistent
result, the Evaluator fails closed: ``plan_done`` with a pending step routes to
the Planner (replan); an all-resolved plan with no overall is completed to the
answer (the work is done).

After the plan updates, the router derives the next edge from the plan state:
``completed`` -> answer, ``aborted`` -> failure answer, a ``replan_reason`` ->
Planner, otherwise the next batch by kind.

It judges the goal and each step's criterion against the **outcome**, not
merely whether tools succeeded; ``executed_tools`` is rendered grouped by step
and the observations are per step, so a different tool that meets the criteria
is fine, while a material mismatch is reported in the step's ``reason``.  Its
user prompt also carries an optional ``recent_tool_failures`` block from the
failed-batch digest, so a failed call is not mistaken for evidence of success.
It never writes ``message_for_user``.  An empty or failed evaluation escalates
deterministically to a replan.  Scientific mode uses a separate, independent,
epistemic verifier with the same judge-only contract.

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
answer explains the real cause.

## Plan history and replan feedback

``plan_history`` is a bounded FIFO of the last 9 superseded plans.  Each entry
stores the full steps and the reason the plan was abandoned: the automated
``replan_reason``, human review feedback, or supplied answers.  The active plan
is not duplicated in the list; the Planner prompt renders the previous versions
and explicitly tells the model not to repeat an abandoned approach and to state
what changed.  The history is independent of ``automated_plan_revisions``,
which resets when a human supplies input.

There is no deterministic repeat detector.  Two differently worded plans can
describe the same failed approach, and exact text/structure comparison would
either miss that or reject legitimate revisions, so the system does not pretend
to judge semantic similarity.  It gives the Planner the history, bounds the
loop with the automated-replan budget, and hands the decision to a human at
exhaustion.

On exhaustion the Planner preserves the plan and writes ``needs_input`` with a
blocked guidance step.  The ordinary input HITL ask includes sections for the
previous and current plan versions, so the user can see what was tried and why
it failed.  The answer re-enters the Planner as ``human_input``; the automated
budget resets, while ``plan_history`` is retained.  Routing stays on the normal
``needs_input`` path; the run is not marked ``unplannable``.

## Termination and budgets

The deterministic guards bound every loop and mirror RAG's ``RouteEvaluator``:
counters live in state (incremented by the acting nodes) and the caps are
enforced deterministically in the Evaluator/Planner ``_update_state`` (not by
the LLM).  ``tool_retry_counts`` (triage) and ``step_attempt_counts`` escalate
to a replan; ``automated_plan_revisions`` exhaustion preserves the plan and
asks the human for guidance via ``needs_input``, so the act/eval loop always
terminates or pauses for a human.

The plan length is deliberately **not capped**: long-horizon research tasks may
need many focused steps.  A single oversized plan can still consume tokens, so
the Planner prompt requires one action/observable outcome per step and forbids
compressing independent work into a giant step.  The execution loop is bounded
by the per-step and replan budgets, not by the number of steps.

The Evaluator prompt uses ``step_incomplete`` only when a specific further call
is expected, ``need_replan`` when the observations show no progress toward the
criterion, and ``abort`` when they show the goal is unreachable (above).

## Fault tolerance and resume

A node exception stops the run at the failed node; the checkpoint keeps the
last successful super-step, so the run stays resumable.  Two mechanisms
(ADR-0043):

* Transient LLM timeouts (e.g. a stream that stalls after its response
  starts) are retried inside ``BaseLLMNode._invoke_with_retries`` with a
  bounded backoff; rate limits stay with the provider SDK (which retries
  ``429``/``5xx`` and honours ``Retry-After``).
* A failed run is resumed by invoking the thread with ``None`` (no query),
  which re-runs only the failed node and downstream - completed tool side
  effects are not repeated.  ``/query`` and ``/query/stream`` expose this as
  a ``resume=true`` flag (no query) and the web UI offers a Retry action on a
  ``resumable`` error.  A graph-rendered failure answer is deliberately not
  produced: it would run to ``END`` and foreclose resume.

The user turn is persisted when the run starts and the assistant reply on
completion, so a failed turn is recorded and a resume completes the same turn
without duplication (one user row, one assistant row).  Confirmed LangGraph
semantics are in ``devdocs/system/graph-resume.md``.

## Answer synthesis

``AnswerFromResults`` runs once when the plan is ``completed``
(success) or ``abort``/``unplannable`` (failure).  (A ``needs_input`` plan never reaches it:
it pauses at the HITL input node.)  On success it writes ``message_for_user``
from the goal, the completed plan, the observations and the session artefacts
(so the reply can draw on earlier tasks' deliverables), and persists the
deliverable to ``artefacts`` (see Persistence); on failure it explains concisely
what was attempted and why it could not be completed (using
``failure_reason``, the plan and the observations).  The outcome-specific
detail (the failure reason) is rendered as a single conditional block, omitted
entirely on success.  It judges nothing.  Its prompt
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

``ToolsPicker``/``ToolsCallerNode`` are shared with RAG (ADR-0020); the agent
uses ``ToolCallBinder``, a ``ToolsPicker`` subclass that binds the Planner's
chosen tools one group at a time.  The evaluator and planner are agent nodes;
the general evaluator contract (operational) differs from the scientific
evaluator contract (epistemic), even if code is shared.
