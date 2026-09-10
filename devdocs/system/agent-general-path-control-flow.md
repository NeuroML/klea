# General-path control flow

Status: design note, not an ADR.  Concrete mechanics for ADR-0035; to be
replaced by the C4 agent component diagram once the graph is implemented.
Written 2026-09-10 by opencode (model: deepseek-flash).

## Scope

Mechanics of the general (operational) path: nodes, state, failure triage,
evaluator routing and escalation.  Scientific mode is the general base plus the
ADR-0029 correctness layer (ADR-0036).

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
RouteDecision (answer | act | plan)
  |-- answer -> AnswerUser -> END
  |-- act ----> Act
  '-- plan ---> GoalSetter -> Planner -> Act

Work loop:
  Act (ToolsPicker + ToolsCaller)
    -> TriageRouter (deterministic)
         +-- error, retries left -> Act
         +-- retries exhausted   -> GoalSetter -> Planner -> Act
         +-- no error            -> Evaluator (operational)
                                      +-- step_incomplete -> Act
                                      +-- step_done       -> Act (next step)
                                      +-- need_replan     -> GoalSetter -> Planner -> Act
                                      +-- plan_done       -> AnswerUser -> END
```

Trivial chat: Guard plus one RouteDecision call (the route node answers
inline, `answer`).  No planner, no tools, and no evaluator ceremony beyond the
operational check.

## Nodes

| Node | Role | LLM |
|------|------|-----|
| InitGraphState | seed state | no |
| ModeDecision / ModeInformer | resolve mode (ADR-0030) | no |
| Guard | safety classification (ADR-0010) | yes (guard role) |
| RouteDecision | upfront plan decision: answer inline, act, or plan (ADR-0035) | yes (chat role) |
| GoalSetter | sole writer of the immutable goal + task success criteria; skipped if already set | yes (plan role) |
| Act (ToolsPicker + ToolsCaller) | emit ``ToolCallsSchema`` or an inline answer; dispatch parallel tool calls (ADR-0020/0034) | picker yes, caller no |
| TriageRouter | deterministic: error present? retries left? | no |
| Evaluator | operational: goal + step criterion; explicit verdict enum | yes (chat role; separate node from Act) |
| Planner | create and revise the evolvable plan | yes (plan role) |
| AnswerUser | final user-facing message | no |

## Route classification

The RouteDecision route is judged from the request, the conversation and a
coarse capability summary, not from tool schemas.  Three cases:

| Case | Route | Examples |
|------|-------|----------|
| chat | ``answer`` | "Explain Hodgkin-Huxley gating"; "What did we conclude about the parameters?" |
| trivial task | ``act`` | "List the files here"; "Show me cells/granule.cell.nml"; "Validate model.nml"; "Find every use of iafCell" |
| multi-step task | ``plan`` | "Add a persistent sodium channel and validate it"; "The simulation diverges at t=5 ms; find out why and fix it"; "Compare all cell models and write up the differences" |

Rubric:

1. Does it need the environment/tools at all?  No -> ``answer``.
2. One independent operation, no dependency, no verification beyond the tool
   result? -> ``act``.
3. Multiple dependent steps, discovery, or an end-state needing
   checking/iteration? -> ``plan``.

The boundary is a gradient ("summarise this file" is ``act``; "summarise every
paper in this folder" is ``plan``), so misrouting must be cheap and
recoverable: an ``act`` that turns out complex escalates to the Planner via
triage/evaluator, and a needless ``plan`` costs one planner call.

## Tool disclosure

The same ``tools_info`` built by ``BaseLangGraph`` is disclosed at three
levels:

| Node | Tool info | Rationale |
|------|-----------|-----------|
| RouteDecision | coarse capability summary, or nothing | routing is about work shape; keep the router cheap |
| Planner | name + docstring, no parameter list | enough to plan executable steps and suggest real tools |
| ToolPicker | full description incl. parameter list | needs argument detail to emit ``ToolCallsSchema`` |

The Planner sees all configured tools (bundled plus domain).  Pruning or
filtering is deferred; if it becomes necessary, prefer multi-domain selection
(ADR-0011) or tool retrieval over single-domain classification, because domain
classification is fragile for multi-domain tasks and for domains that emerge
mid-plan.

Caching: v1 keeps the tool block stable so provider prefix caching (ADR-0028)
still applies.  Narrowing the picker's tool set per step (for example to the
planner's ``suggested_tools``) changes the block and loses that cache; it is a
deferred, harness-tested experiment, and it is provider-dependent (local stacks
may not cache at all).

Planner and ToolPicker stay separate nodes: the Planner runs once (or on
replan) without per-step outputs, while the picker runs after each batch with
the latest results so it can set arguments that depend on prior outputs.
Merging them would force an upfront ReWOO-style plan of concrete calls and a
large compound emission that small models handle poorly; the picker is also
shared with RAG (ADR-0020).

## State (relevant fields)

* ``goal: GoalSchema`` (goal and success criteria; immutable, written only by GoalSetter).
* ``plan: PlanSchema`` (step list with per-step ``success_criteria``, status,
  current step index).
* ``step_outputs: dict[int, list[CallToolResult]]`` (per-step results).
* ``tool_calls`` / ``tool_results`` (current batch; shared with RAG).
* ``step_retry_counts`` (to add): attempts per step for the ADaPT policy.
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
* Level 3, human: ambiguity -> ask; consequential actions -> approval.

General mode prioritises level 0; level 2 is operational and results are
unverified.  Scientific mode requires level 1 criteria and an independent
level 2 verifier with provenance.

## Failure triage

| Cause | Route | Notes |
|-------|-------|-------|
| transient (network, timeout, 5xx) | retry at dispatch | bounded (ADR-0017 handles LLM; dispatch handles tools) |
| call-level (bad args, wrong tool, permission denied) | picker | re-pick with the error fed back |
| step-level (criterion unsatisfiable, step ill-specified) | planner | revise the plan |
| repeated failure of the same step | planner | ADaPT policy: N re-picks with error, then escalate |

Failure is attributed per call, not per batch: successful calls in a batch are
kept, failed calls are re-picked.

## Goal handling

``GoalSetter`` is the sole writer of ``state.goal`` (the goal plus task-level
success criteria) and runs only when entering the plan path; ``_pre_exec``
skips it if the goal is already set.  The Planner reads the goal but never
writes it, so a replan cannot move the success reference.  On the ``act`` path
the goal is unset and the Evaluator judges against the query; the goal is
frozen as soon as planning begins.  Every Planner entry (initial plan,
escalation, replan) passes through GoalSetter for this reason.

## Evaluator verdicts and routing

The Evaluator runs after every Act batch.  In general mode it also writes
``message_for_user`` when the task is done (it doubles as answer synthesis), so
it is not an extra call over a separate answer node.  Its verdict is a pydantic
model whose next-step is a ``Literal``:

* ``step_incomplete`` -> Act (more calls for the current step)
* ``step_done`` -> Act (next step) if steps remain
* ``plan_done`` -> AnswerUser
* ``need_replan`` -> GoalSetter -> Planner

On the ``act`` (planless) path only ``plan_done`` (answer written) and
``need_replan`` occur.  The Evaluator judges the goal (or the query when no
goal is set) and the current step's criterion, not merely whether tools
succeeded.  Scientific mode uses a separate, independent, epistemic verifier
instead of this operational Evaluator.

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
