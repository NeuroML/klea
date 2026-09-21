---
status: "accepted"
date: 2026-09-21
decision-makers: Ankur Sinha
consulted: "opencode (design sessions 2026-09-19 and 2026-09-21)"
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
* A tool's effect *kind* is known deterministically (`ToolInfo` carries
  `read_only`, `idempotent` and `destructive` MCP annotations,
  `mcp/schemas.py:46-49`; ADR-0037), and its *resource* arguments are declared
  by `ToolInfo.checkpaths` (`registry.py:170-176`).  The resource - not the
  effect kind - is what decides whether two calls conflict.
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

Accepted: **B - atomic steps plus explicit `depends_on`, executed by a
deterministic frontier scheduler**, with a **caller-side resource guard**
rather than the effect gate the draft proposed.  This supersedes the "no
cross-step DAG scheduling" clause of ADR-0035 (only that clause; the rest of
ADR-0035 stands).

The plan is a directed acyclic graph over the ordered step list: `depends_on`
names the earlier steps a step needs.  `PlanSchema.validate_plan` enforces
positive/unique step numbers, in-plan references, references only to
*strictly earlier* steps, and acyclicity (`graphlib.TopologicalSorter`).  Code
computes the **frontier** - the pending steps whose dependencies are all
`done` - and dispatches it.

Work progresses in a **batch**: the frontier (capped at 8 steps / 16 calls by
default, configurable) is bound in one picker invocation.  Each call carries
its originating step and the calls are flattened in step order.  The caller
groups the bound calls by **resource** (a tool's `checkpaths` arguments,
compared lexically with `os.path.normpath`) and runs calls that share a
resource **sequentially in step order**; every other call runs concurrently
(`asyncio.gather`).  A tool whose resource cannot be identified runs
concurrently, as today; resource paths are neither resolved nor validated.

The Evaluator judges the whole batch at once - given the batch's steps (with
their success criteria), the executed tools and the per-step observations - and
returns a verdict **per step** (a map), so one evaluation round covers the
frontier.

### Controls that make this safe

1. **Structural validation.** `PlanSchema.validate_plan` rejects duplicate or
   non-positive step numbers, dangling `depends_on`, non-backward references,
   and dependency cycles (`graphlib.TopologicalSorter`).  LLM dependency data
   therefore affects performance, not well-formedness.
2. **Serialize by resource conflict.** The caller runs same-resource calls in
   step order; everything else is concurrent.  A missed dependency edge costs
   parallelism, never a silent lost update.
3. **Unknown resources run concurrently.** A call whose resource cannot be
   identified is not serialised or validated - today's behaviour.
4. **Binding check (retained).** A step whose argument depends on an earlier
   step's output cannot be bound by the picker (the value is absent); it
   escalates to the Planner.

### Why not the effect gate

The draft proposed gating concurrency on `read_only`/`idempotent` annotations.
That rejects legitimate parallelism (independent writes to different files)
and does not actually identify conflicts: the annotations describe an effect
*kind*, not the *resource*.  A deterministic caller-side resource guard uses
the bound arguments, where the information actually exists, and cannot be
fooled by a missing dependency edge: two writes to the same file serialise
even when `depends_on` is absent.

Dependencies that are not resource-local (for example "download, then read")
are not caught deterministically, but they fail loudly - the later call errors
- and the Evaluator loop returns that failure to the Planner.  The silent
class, same-resource read-modify-write, is what the caller guard closes.

### Consequences

* Good, because a wrong or missing resource dependency cannot corrupt results:
  same-resource calls serialise in step order.
* Good, because the scheduler is deterministic and inspectable, and uses the
  existing `checkpaths` metadata rather than asking a model to judge safety.
* Good, because atomic steps keep retry semantics (same tool, arguments only)
  and per-step `success_criteria` simple, and independent writes parallelise.
* Bad, because it needs a new plan-state model (per-step statuses and a
  frontier, replacing `current_step_index`) and per-step Evaluator verdicts.
* Bad, because it supersedes part of an accepted decision (ADR-0035), and the
  parallelism win is unmeasured until the harness case lands.
* Neutral, because the intra-step multi-call allowance becomes the batch
  binding capability.

### Confirmation

* Deterministic unit tests for `frontier()`: only steps whose dependencies are
  `done` appear; `depends_on` requires strictly earlier references; a cycle is
  rejected (`graphlib.CycleError`).
* Deterministic unit tests for the resource guard: same-resource calls run in
  step order, different-resource calls run concurrently, unknown-resource
  calls are not serialised.
* A graph test where two independent steps dispatch in one frontier, and the
  same steps with a declared dependency do not.
* A batch cap test: the frontier is truncated to the configured step/call cap.
* Prompt/schema tests: the Planner authors an acyclic DAG; the Evaluator
  returns a per-step verdict map.

Implemented 2026-09-21: `frontier()`/`next_batch()` and the `validate_plan`
extensions (strictly-earlier `depends_on` + `graphlib` acyclicity) in
`agent_pkg/klea_agent/schemas.py`; `resource_key()` and the caller-side
resource grouping in `utils_pkg/klea_utils/mcp/dispatch.py`; the batch picker
and per-step Evaluator map (`ToolCallSchema.step`, `EvaluationSchema`).

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
  caller-side resource guard prevents correctness damage.
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

Decisions now settled (with the implementation increments):

* **Plan-state model.** Per-step `status` plus a computed `frontier()`
  replaces the single `PlanSchema.current_step_index`
  (`schemas.py`); the Evaluator advances step statuses and the router
  recomputes the frontier.
* **Fail-safe representation.** `depends_on` is trusted as the Planner's
  ordering; correctness comes from the caller-side resource guard, not from a
  serialize-by-default rule.
* **Planner prompt.** `depends_on` becomes a required, checked field
  (strictly-earlier references, acyclic); the "steps are linear (no
  branching)" line is replaced with plain-language DAG rules.
* **Picker.** One invocation binds the batch (the maximal same-kind prefix of
  the frontier); calls carry their originating step and are flattened in step
  order.

Still open:

* **Reasoning batching.** A batch is currently the maximal *same-kind* prefix
  of the frontier.  Tool steps are genuinely batched (one picker invocation
  binds them all), but reasoning steps remain **serial**: `ReasoningNode`
  produces a single conclusion for the current step, so a reasoning prefix of N
  steps is drained one per round.  Batching it (a per-step conclusion map, like
  the evaluator) is deferred.
* **Measurement.** Build the evaluation-harness case for independent steps and
  confirm the win.
* **Batch cap.** Default 8 steps / 16 calls; revisit once measured.
* **Path normalisation.** Lexical `os.path.normpath` only; absolute-vs-relative
  forms of the same file are not grouped.
* **Interaction with HITL.** How plan review (`AwaitReview`) presents a
  frontier/DAG plan rather than a linear one.

Related decisions and references:

* ADR-0035 general operational agent path (`:85,121,129-134,137-138`), which
  this ADR partially supersedes.
* ADR-0025 (superseded) `:68` "a plan step allows parallel tool calls by
  default".
* ADR-0037 tool access levels and the `ToolInfo` effect annotations;
  `ToolInfo.checkpaths` (`registry.py:170-176`) supplies the resource args.
* ADR-0028 prompt cache (stable prefix).
* Session log `.agents/2026-09-18-2359.md:111` ("dependency-frontier batching
  ADR"), which recorded this as carry-over work.
