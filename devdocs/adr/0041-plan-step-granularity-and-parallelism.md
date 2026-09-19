---
status: "draft"
date: 2026-09-19
decision-makers: Ankur Sinha
consulted: "opencode (design session 2026-09-19)"
informed: klea contributors
---

# Plan step granularity, explicit dependencies and parallel step execution

## Context and Problem Statement

The general agent path (ADR-0035) uses an evolvable plan of ordered steps. Two
statements in that decision are in tension:

* the work loop is linear: `PlanSchema.current_step_index` names a single
  current step, and ADR-0035 states "There is no cross-step DAG scheduling;
  step order encodes dependencies" (`0035:137-138`, reinforced by
  `Planner_system.md:56` "steps are linear (no branching)");
* the shared picker/caller support multiple parallel tool calls per step
  (ADR-0025:68; ADR-0035:85,121; `ToolsPicker_system.md:32`;
  `dispatch_tool_calls` dispatches with `asyncio.gather`).

In practice the Planner emits atomic, single-action steps ("read file A";
"read file B"), so the picker always emits exactly one call and the
intra-step parallel dispatch is never exercised. Parallelism is therefore
designed at the tool-call layer but decided at the action layer, and the two
are unreconciled. The Planner also cannot judge batching reliably: it sees
only compact tool descriptions (name plus docstring, no parameter list,
`planner.py:78-103`; ADR-0035 tiered disclosure).

The question: should the Planner compose steps that carry several parallel
calls (parallelism implicit in the step grouping), or emit atomic steps and
let a deterministic scheduler decide what runs concurrently from explicit
dependency information?

A key observation from the design discussion: **both options rely on LLM
judgement, and neither eliminates bad dependency information.** The useful
design goal is therefore not "a correct dependency graph" but "a wrong
dependency decision costs performance, never result correctness". This is
only achievable if the parallelism decision has an explicit representation
that deterministic code can inspect, gate and override.

Scope: the agent general path (`klea_agent`), the `PlanSchema`/`StepSchema`
plan model, and the surrounding work loop (Picker, Caller, TriageRouter,
Evaluator). It does not change RAG, which has no plan.

## Decision Drivers

* Correctness must not depend on the accuracy of LLM-produced dependency
  information.
* Tool effects are already known deterministically: `ToolInfo` carries
  `read_only`, `idempotent` and `destructive` MCP annotations
  (`mcp/schemas.py:46-49`; ADR-0037), and the tool picker/caller already gate
  on them.
* Retry and per-step success criteria stay simple when a step is one action,
  one tool identity and (ideally) one call; partial-batch failure and
  compounded success criteria are avoided.
* Inspectability (ADR-0013): the parallelism decision should be visible and
  explainable, not implicit in how the model happened to group calls.
* Small-model viability: no large compound emission that asks one call to
  both decompose and batch (ADR-0035:129-134).
* Prompt-cache stability (ADR-0028): do not make per-step prompts more
  volatile than necessary.
* Clean boundary with Scientific mode (ADR-0030/0036): this is an operational
  scheduling concern.

## Considered Options

* A. Planner composes multi-call steps; parallelism is implicit in the
  grouping.
* B. Atomic steps plus explicit `depends_on`; a deterministic
  dependency-frontier scheduler decides what runs concurrently.
* C. No parallelism: strictly sequential atomic steps, removing the
  multi-call allowance and its dead machinery.

## Decision Outcome

Draft -- not yet decided. The current leaning is **B, conditional on the
deterministic controls below**, with the explicit understanding that this
requires more thought and is revisited before implementation. It would
supersede the "no cross-step DAG scheduling" clause of ADR-0035 (only that
clause; the rest of ADR-0035 stands).

The controls that make B different from A in terms of safety, and which are
the actual subject of the decision:

1. **Effect gate.** Only steps whose tool is `read_only` or `idempotent` (and
   not `destructive`) may run concurrently. Any mutating step serializes,
   regardless of what the Planner claims. Because each step carries a tool
   identity under the picker/planner contract, the scheduler can look this up
   from `tools_info`.
2. **Fail-safe default.** Assume dependence (serialize) unless independence is
   explicitly declared. A missing dependency then costs only parallelism,
   never correctness. (Note the current representation is the opposite:
   `depends_on: []` means "no dependencies" and would run immediately.)
3. **Structural validation.** `PlanSchema.validate_plan()` already rejects
   dangling `depends_on` references and duplicate step numbers
   (`schemas.py:152-190`); a frontier scheduler additionally requires an
   acyclic graph, checked deterministically.
4. **Binding check.** A step whose arguments actually depend on an earlier
   step's output cannot be bound by the picker (the argument is absent) and
   fails to the Planner. A dependent-but-declared-independent step is thus
   caught at execution, not silently mis-executed.

With these, LLM dependency data affects performance and replan frequency, not
result correctness. Without controls 1-2, B is no safer than A and should not
be pursued.

Because the mode of execution changes (a single current step vs a set of
runnable steps), this decision also requires a plan-state model change (see
Open Questions); that is the main reason it is left as a draft.

### Consequences

* Good, because a wrong parallel/dependency judgement cannot corrupt results:
  writers serialize and the default is serialized.
* Good, because the scheduler is deterministic and inspectable, and reuses the
  existing MCP effect annotations rather than asking a model to judge safety.
* Good, because atomic steps keep retry semantics (same tool, arguments only)
  and per-step `success_criteria` simple.
* Bad, because it needs a new plan-state model and evaluator changes (multiple
  runnable steps; per-step statuses rather than one `current_step_index`).
* Bad, because it supersedes part of an accepted decision (ADR-0035), and the
  parallelism win is currently unmeasured.
* Neutral, because the intra-step multi-call allowance becomes optional and
  possibly removable; the decision about keeping it is deferred.

### Confirmation

To be defined once a direction is chosen. Candidate checks:

* Deterministic unit tests for the scheduler: only `read_only`/`idempotent`
  steps ever appear in the same frontier batch; a mutating step is never
  co-scheduled; a dependency cycle is rejected at plan validation.
* A graph test where two independent read-only steps are observed to dispatch
  in one frontier, and the same steps with a declared dependency do not.
* Prompt/schema tests: each step carries a tool identity; `depends_on`
  validates.

## Pros and Cons of the Options

### A. Planner composes multi-call steps

* Good, because parallelism needs no scheduler: it is one step, one dispatch.
* Neutral, because a step can carry several calls of the same tool.
* Bad, because the parallelism decision is implicit in the grouping and has no
  field to inspect, validate or override.
* Bad, because a wrong grouping of dependent calls is a silent race with no
  deterministic backstop.
* Bad, because the Planner cannot judge batching: it does not see parameter
  schemas (`planner.py:78-103`).
* Bad, because per-step `success_criteria` and retry become compound
  (partial-batch success/failure).

### B. Atomic steps plus explicit `depends_on` and a deterministic frontier

* Good, because the dependency claim is explicit and structurally validatable.
* Good, because the LLM's dependency data becomes a performance hint: the
  effect gate and serialize-by-default rule prevent correctness damage.
* Good, because atomic steps keep retry and success criteria simple.
* Bad, because it needs a new plan-state model, a scheduler and evaluator
  changes.
* Bad, because it relies on `depends_on` being useful; if the Planner
  under-reports dependencies, parallelism is lost or replans increase (but
  correctness holds).
* Neutral, because `depends_on` and per-step `status` already exist in
  `StepSchema` and are scaffolded but currently informational.

### C. No parallelism

* Good, because the simplest and safest: remove the dead multi-call machinery,
  keep linear atomic steps.
* Good, because it keeps the existing single-`current_step_index` plan state.
* Bad, because it forgoes concurrent independent reads, which is where the
  latency win lives.
* Bad, because it leaves the Planner's `depends_on` field with no purpose.

## More Information

Open questions to settle before accepting this ADR:

* **Plan-state model.** Replace the single `PlanSchema.current_step_index`
  (`schemas.py:113`) with per-step statuses and a runnable set/queue; how the
  Evaluator advances a frontier and how `plan_done` is detected
  (`operational_evaluator.py:163-178`).
* **Fail-safe representation.** How to express "serialize unless independent":
  invert the `depends_on` default, add an explicit independence flag, or
  require the Planner to enumerate the stable/no-arg steps it can batch.
* **Planner prompt.** Whether to keep `depends_on` advisory or make it a
  required, checked field, and how to prompt it without asking the model to
  reason about tool effect annotations.
* **Picker multi-call allowance.** Keep the intra-step multi-call capability
  for picker-driven batching of intent-level steps, or restrict the picker to
  one call per step (which makes partial-batch retry moot).
* **Measurement.** Build the evaluation-harness case for independent reads
  and confirm the win before committing to the scheduler complexity.
* **Interaction with HITL.** How plan review (`AwaitReview`) presents a
  frontier/DAG plan rather than a linear one.

Related decisions and references:

* ADR-0035 general operational agent path (`:85,121,129-134,137-138`), which
  this ADR would partially supersede.
* ADR-0025 (superseded) `:68` "a plan step allows parallel tool calls by
  default".
* ADR-0037 tool access levels and the `ToolInfo` effect annotations.
* ADR-0028 prompt cache (stable prefix).
* Session log `.agents/2026-09-18-2359.md:111` ("dependency-frontier batching
  ADR"), which recorded this as carry-over work.
