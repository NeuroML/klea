# Planner evidence rendering: depends_on-closure relevance selection

Status: system note. Refines the evidence-rendering part of ADR-0035 and ADR-0054; it does not supersede either decision.

## Decision

The Planner's prompt receives prior work through `observations`, rendered by
`KleaAgentState.observations_text()` and gated to `route == "continue"` for the
bounded `previous_work` block (`agent_pkg/klea_agent/nodes/planner.py:177-193`).

No `references` field is added to `StepSchema` or to any other schema. The
consumption edge is `depends_on`, read transitively: a step that reuses an
earlier step's payload declares that step in `depends_on`, and the renderer
selects earlier-step payloads by an ordered relevance rule (below). Anything
not selected degrades to `StepOutput.summary()` plus a spill pointer rather
than disappearing.

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

The whole block stays bounded by the continuation caps, including
`MAX_CONTINUATION_RENDER_CHARS = 8000` (`agent_pkg/klea_agent/schemas.py:469-477`)
and the `ContinuationEvidence.render()` clipping
(`agent_pkg/klea_agent/schemas.py:508-572`).

Review steps are never payload sources. A `review` step's "execution" is the
user's approve/revise decision, it names no tools and is not judged by the
Evaluator (`agent_pkg/klea_agent/schemas.py:30-55`), so it produces no
`StepOutput` and resolves to an empty render.

## Fallback: summary plus spill pointer

Everything not selected still renders, in compressed form: `StepOutput.summary()`
(the tool name and ok/error, or the reasoning conclusion's first line;
(`agent_pkg/klea_agent/schemas.py:887-906`), a stable step id, and a pointer to
the full payload.

The pointer is constrained to the project root. It must never name
`~/.cache/nml_mcp`: that path is outside the read boundary and is wiped at
shutdown, so a pointer into it would be stale by construction.

The Planner holds no bound tools, so recovery from a pointer is a *delegated*
read step in the plan, not an inline load. Artefacts cannot serve as the spill
store either: they are in-memory only
(`agent_pkg/klea_agent/schemas.py:580-608`) and cannot be loaded by id.

## Residual risk

One failure mode remains: a Planner writes `depends_on` without the step whose
output it actually reuses. The effect is bounded, not silent, because the
missing step still falls to tier 4 (last completed step) or to a delegated read
step recovered from its spill pointer. Adding a `references` field would fail in
the same way for the same reason (the model must still declare the edge), which
is why it is withdrawn rather than layered on.

## References

- `agent_pkg/klea_agent/nodes/planner.py:177-193` - `_get_prompt_variables`:
  `previous_work` gated on `route == "continue"`; `observations` via
  `state.observations_text()`.
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
