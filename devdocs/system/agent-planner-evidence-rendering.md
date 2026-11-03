# Planner evidence rendering: depends_on-closure relevance selection

Status: system note. Refines the evidence-rendering part of ADR-0035 and ADR-0054; it does not supersede either decision.

## Decision

The Planner's prompt receives prior work through `observations`, rendered by
`KleaAgentState.planner_evidence()` and gated to `route == "continue"` for the
bounded `previous_work` block (`agent_pkg/klea_agent/nodes/planner.py:177-193`).

`planner_evidence()` is a delegate, not a move: it derives the evaluator-flagged
set from the state's `step_incomplete` verdicts and the latest abandoned plan
revision from `plan_history`, then passes the plan, the recorded `step_outputs`
and those two inputs to `render_plan_evidence`, which applies the selection rule
of this note. The method's docstring defers to this note for that rule. The
previous `KleaAgentState.observations_text()` is retained for the
ReasoningNode and the tools picker, which need the flat, step-ordered rendering
it provides. The answer node does not use it:
`AnswerFromResults._observations_text` calls `render_step_observations` with
`MAX_OBSERVATIONS_CHARS = 48_000`
(`agent_pkg/klea_agent/nodes/answer_from_results.py:46`, `:95-111`), which admits
steps newest-first with errored steps first; a payload demoted past the budget
keeps only `StepOutput.summary()` plus its step reference and is not retrievable
within the run. The Evaluator does not use it: its
observations cover only the current batch plus the batch's transitive
`depends_on` closure (`OperationalEvaluator._observations_text`,
`agent_pkg/klea_agent/nodes/operational_evaluator.py:102-118`).

No `references` field is added to `StepSchema` or to any other schema. The
consumption edge is `depends_on`, read transitively: a step that reuses an
earlier step's payload declares that step in `depends_on`, and the renderer
selects earlier-step payloads by an ordered relevance rule (below). Anything
not selected degrades to `StepOutput.summary()` plus a step reference rather
than disappearing; the spill-pointer half of that fallback is designed but not
implemented (see Fallback).

## Why depends_on suffices

The Planner prompt defines `depends_on` as the earlier steps a step needs, and
states that a step with no dependencies may run in parallel while a step runs
once its depended-on steps are `done` or `skipped`
(`agent_pkg/klea_agent/nodes/prompts/Planner_system.md:84-85`, `:97-101`). The
runtime frontier implements exactly that reading: a pending step is unblocked
when every number in `depends_on` is resolved
(`agent_pkg/klea_agent/schemas.py:230-252`).

This is already the shape a consuming plan takes. Independent reads render as
`(depends on: (none))`, and the step that combines them names both, for example
`(depends on: 5, 6)` (`agent_pkg/klea_agent/schemas.py:87-131`). The plan
therefore already records *which* earlier output a step consumes; no second
edge type is needed.

Two mechanical points follow.

- `depends_on` is a list of direct edges only
  (`agent_pkg/klea_agent/schemas.py:47`).
- `validate_plan` enforces positive unique numbers, earlier-only references and
  acyclicity, but never completeness of the dependency set
  (`agent_pkg/klea_agent/schemas.py:296-342`).

So the selector must expand the direct edges transitively (the closure of every
pending step), and it must not assume the Planner declared every edge it uses
(see Residual risk).

## Selection rule

The selector applies priority-ordered tiers and renders each selected entry in
full with `StepOutput.render()` (`agent_pkg/klea_agent/schemas.py:843-885`):

1. Steps whose latest attempt failed (the most recent errored entry only, not
   superseded failures).
2. The transitive `depends_on` closure of every pending step.
3. Steps the Evaluator flagged, as carried by `step_incomplete` verdicts
   (`agent_pkg/klea_agent/schemas.py:779-799`).
4. The last completed step.
5. The latest abandoned plan revision
   (`agent_pkg/klea_agent/schemas.py:436-457`).

The render is bounded by a character budget: `MAX_EVIDENCE_CHARS = 24_000`
(`agent_pkg/klea_agent/evidence.py:66`), overridable per call. Full renders are
admitted in tier priority order until the next one would push the block past
the budget; that step and every later selected step fall back to
`StepOutput.summary()` plus their step reference
(`agent_pkg/klea_agent/evidence.py:249-264`), so a demoted step keeps its
number and reference and no selected step disappears. The 80-character cap
inside `StepOutput.summary()` (`agent_pkg/klea_agent/schemas.py:811`) bounds
only the summarised entries; admitted full renders are not truncated.
Separately, `MAX_STEP_RESULTS = 20`
(`agent_pkg/klea_agent/klea_agent.py:98`; eviction at `:512`, `:569`) is a
per-step retention cap on the results recorded for one step, not a plan-wide
total of tool calls. The
continuation caps
(`agent_pkg/klea_agent/schemas.py:469-477`) and the
`ContinuationEvidence.render()` clipping (`agent_pkg/klea_agent/schemas.py:508-572`)
bound `previous_work`, not this `observations` block.

The cap position is the selection loop, not the renderer. The intended mechanism
is a priority-ordered accumulation over the tier list (failed, `depends_on`
closure, Evaluator-flagged, last completed, abandoned): add each `_render_full`
block while the running character total stays inside a configured budget, and
render every step that does not fit through `_render_summary()`
(`agent_pkg/klea_agent/evidence.py:180-203`). It never splits a payload, so the
highest-priority evidence stays intact and each demoted payload is still named
and recoverable. The precedent is `get_recent_messages(messages, max_chars,
keep_at_least=1)` (`utils_pkg/klea_utils/llm.py:2027-2051`). Rejected: clipping
the assembled text with `_clip_text`
(`agent_pkg/klea_agent/schemas.py:480-485`) or `truncate_middle`
(`klea_utils/mcp/path_detect.py:55-74`), which cuts the last payload mid-content
and still leaves the total order-dependent; or a fixed per-entry cap inside
`_render_full`, which mangles every payload and still does not bound the total.
The budget value is a configurable safety margin, not a claim about what content
matters.

Review steps are never payload sources. A `review` step's "execution" is the
user's approve/revise decision, it names no tools and is not judged by the
Evaluator (`agent_pkg/klea_agent/schemas.py:30-55`), so it produces no
`StepOutput` and resolves to an empty render.

## Fallback: summary plus step reference

The implemented fallback is `StepOutput.summary()` (the tool name and ok/error,
or the reasoning conclusion's first line;
`agent_pkg/klea_agent/schemas.py:887-906`), a stable step number, and a step
reference, produced by `_render_summary`
(`agent_pkg/klea_agent/evidence.py:132-139`), rendered as
`- Step N: full payload omitted; retrieve it by planning a step that depends on N`.

Spill pointer: designed, not implemented. The design would append a pointer to
the full payload. It is not built, and a render-time pointer cannot be promised
by the current retention policy: the recorder keeps only the latest
`MAX_STEP_RESULTS = 20` results per step
(`agent_pkg/klea_agent/klea_agent.py:98`), evicting older ones at record time,
so a step number alone does not guarantee that the payload it names still
exists.

If implemented, the pointer must be constrained to the project root. It must
never name `~/.cache/nml_mcp`: that path is outside the read boundary and is
wiped at shutdown, so a pointer into it would be stale by construction.

The Planner holds no bound tools, so recovery is a *delegated* read step in the
plan (a step that declares the number in `depends_on`), not an inline load.
Artefacts cannot serve as the spill store either: they are in-memory only
(`agent_pkg/klea_agent/schemas.py:580-608`) and cannot be loaded by id.

## Residual risk

One failure mode remains: a Planner writes `depends_on` without the step whose
output it actually reuses. The effect is bounded, not silent, because the
missing step still falls to tier 4 (last completed step) or to a delegated read
step recovered from its step reference (and, if built, its spill pointer).
Adding a `references` field would fail in the same way for the same reason (the
model must still declare the edge), which
is why it is withdrawn rather than layered on.

## References

- `agent_pkg/klea_agent/nodes/planner.py:177-193` - `_get_prompt_variables`:
  `previous_work` gated on `route == "continue"`; `observations` via
  `state.planner_evidence()`, which delegates to `render_plan_evidence`.
- `agent_pkg/klea_agent/schemas.py:30-131` - `StepSchema.kind`, `depends_on`,
  `status_label` and `render`.
- `agent_pkg/klea_agent/schemas.py:296-342` - `validate_plan`.
- `agent_pkg/klea_agent/schemas.py:469-477` - continuation caps.
- `agent_pkg/klea_agent/schemas.py:508-572` - `ContinuationEvidence.render`.
- `agent_pkg/klea_agent/schemas.py:843-906` - `StepOutput.render` and
  `StepOutput.summary`.
- `agent_pkg/tests/test_planner.py:490-512` - existing `previous_work`
  assertions.
- `devdocs/system/agent-node-contracts.md` - row 91 (`continuation_evidence`)
  and section 5.9 (`Planning`).
- `devdocs/system/agent-general-path-control-flow.md` - 126-130 (continue vs
  task), 320-323 (Planner/binder split), 325-339 (parallel execution), 456-462
  (artefacts), 479-483 (`previous_work`).
- `devdocs/system/prompt-conventions.md` - Rule 2 (required vs conditional
  fields), Rule 3 (`_optional_section`).
- `devdocs/adr/0054-cross-run-continuity.md` - Planner `previous_work`
  contract.
- `devdocs/adr/0035-general-operational-agent-path.md` - Planner/reasoning
  observations contract.
