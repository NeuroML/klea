# Agent evaluation harness: planning note

Status: draft plan, not an ADR.  To be refined before implementation.
Companion to `agent-topology-literature-review.md`.  Written 2026-09-10 by
opencode (model: deepseek-flash).  The concrete `eval_pkg` implementation
plan is captured at the end of this document; it is deferred until the
general path stabilises.

## Why

The general-path topology must be chosen on evidence, and the scientific
write-up needs an evaluated comparison.  RAG was not evaluated this way; the
agent is harder because it carries out tasks rather than answering a single
query, so task success and cost are both first-class outcomes.  An evaluation
harness is therefore part of the architecture work, not an afterthought.

## Goals and non-goals

Goals:

- Compare candidate topologies (for example flat ReAct vs escalation ladder vs
  plan-first) on the same tasks, tools and models.
- Measure both correctness/task success and cost (tokens, LLM calls,
  wall-clock, notional money) under non-frontier models.
- Be reproducible and paper-ready: raw JSON plus generated summary tables.

Non-goals (first iteration):

- Full reproduction of GAIA/tau-bench/SWE-bench.  Start with a Klea-specific
  suite that mirrors their task shapes, and optionally add a public subset
  later.
- Multi-agent comparisons.  Out of scope per the literature review.

## What to measure

Per task run:

- task success (deterministic check where possible, LLM judge otherwise)
- pass@1 and pass^k over N repetitions (reliability)
- number of LLM calls, broken down by role
- input/output tokens and cache-read tokens (the node stream already surfaces
  `TokenUsage` via ADR-0013/0028)
- wall-clock time
- number of tool calls, tool errors and retries
- escalation events (structural vs model escalation; which tier handled the
  task)
- estimated cost from a per-model price table

## Task taxonomy

Mirror the failure modes in the literature:

1. Trivial chat (no tools) -- cost floor.
2. Single-tool action (read a file, run a command).
3. Multi-step coding (edit several files, run tests, iterate).
4. Tool-failure recovery (a tool errors; does the agent adapt?).
5. Misleading or under-specified task (does it ask, or guess?).
6. Long-horizon task that exceeds a context window (compaction behaviour).
7. Future: breadth-first research, the only case where multi-agent is
   justified.

Each task ships with a fixture workspace and an explicit success criterion.

## Models

Default condition: **one configured model** must run every topology.  The
general path must be fully functional single-model; multi-model is a separate,
optional condition (model escalation), never a requirement.

- A small local model via the existing ollama setup (CI already pulls
  `qwen3:0.6b`; `bge-m3` for embeddings).
- One mid-size local or hosted non-frontier model.
- Optional: a second model to exercise model escalation.
- Optional: a frontier model as a reference upper bound, clearly marked as
  not the target.

## Isolation and reproducibility

- Each run in a fresh scratch workspace copied from a fixture repo.
- Tools sandboxed through the existing permission boundary.
- Temperature 0 and fixed seeds where possible; N repetitions to compute
  pass^k.
- Pin and record model versions and prompt versions in the result JSON.

## Integration

- Location to decide: a new dev-only package or a `scripts/` entry point.
- Reuse `scripts/run_tests.sh` conventions and a new pytest marker (for
  example `eval`) separate from `localonly`.
- Results written to JSON, with a generated summary table.
- Topology variants wired as swappable graphs sharing prompts, tools and
  models, so the only independent variable is the topology.

## Boundary guardrail

The general path MAY use retrieval (RAG, web search, files) as an optional
tool, but MUST NOT enforce scientific-only stages.  The harness should assert
that the general-mode graph contains no mandatory retrieval/evidence
inspection/gating/independent verification/provenance, and include
retrieval-available tasks without requiring grounding.  This keeps general mode
from silently absorbing scientific complexity.

## Threats to validity

- LLM-judge bias: use rubrics, prefer deterministic checks, spot-check with
  humans.
- Non-determinism and small samples.
- Prompt sensitivity across topologies (keep prompts identical).
- Model version drift over time.

## Open questions

- Which tasks best represent the target academic general use?
- Which models should be the standard set?
- In-repo (pytest/scripts) or a standalone harness package?  (Resolved below:
  a new in-repo `eval_pkg`.)
- First iteration scope: the full taxonomy, or a minimal end-to-end slice?
- How to cost local models: wall-clock/energy, or a notional price table?

---

# eval_pkg implementation plan (deferred)

Status: deferred.  Do not implement until the general agent path has
stabilised (see `agent-general-path-control-flow.md`).  Recorded here so
the design is not lost.  Also needs a general `run_command` tool before
taxonomy items 2 and 3 (single-tool action, multi-step coding) can run.

## Decision

A new dev-only package `eval_pkg/` (import name `klea_eval`), living in the
monorepo alongside the runtime packages, rather than a `scripts/` entry
point or a standalone repository.  Rationale: the harness must import the
orchestrators and subclass them, be unit-testable without an LLM, and share
the existing `ty`/`ruff`/pytest conventions.

Direction of dependency: `eval_pkg` depends on `agent_pkg` and `rag_pkg`
(never the reverse).  It consumes them as installed packages, exactly as the
apps consume `klea_utils`.

## Layout

```
eval_pkg/
├── pyproject.toml / setup.cfg     # package klea_eval; no runtime deps beyond the apps
├── AGENTS.md                      # package-specific commands/conventions
├── klea_eval/
│   ├── common/                    # app-agnostic harness core
│   │   ├── schemas.py             # TaskSpec, FixtureWorkspace, TaskResult, RunConfig
│   │   ├── fixtures.py            # scratch-workspace creation/copy, teardown
│   │   ├── metrics.py             # calls, tokens, cache hits, wall-clock, tool errors
│   │   ├── scoring.py             # deterministic checks + LLM-judge rubric hook
│   │   ├── cost.py                # per-model notional price table
│   │   ├── runner.py              # N-repetition loop, pass@1 / pass^k
│   │   └── report.py              # raw JSON out + generated summary table
│   ├── agent/
│   │   ├── tasks/                 # task set mirroring the taxonomy, with fixtures
│   │   ├── variants.py            # topology variants (see below)
│   │   └── run.py                 # agent-specific entry point
│   └── rag/
│       ├── tasks/
│       └── run.py
└── tests/                         # harness unit tests (no LLM), marker `eval` for full runs
```

## Topology variants

Variants subclass the real orchestrator and override `_create_graph` only, so
prompts, tools and models are shared and the sole independent variable is the
topology:

- variant A: flat ReAct (experimental baseline);
- variant B: plan-first;
- variant E/F: the chosen escalation ladder, and a variant with/without a
  feature under test (for example reasoning steps, or the budgets).

A boundary guardrail test asserts the general-mode graph contains no
mandatory retrieval / evidence-inspection / gating / verification /
provenance nodes, and runs retrieval-available tasks without requiring
grounding (see the guardrail section above).

## Conventions and CI

- `ty.toml`: add `eval_pkg` to `extra-paths` so cross-package imports resolve.
- Tests: `cd eval_pkg && pytest -v` for the unit tests (no LLM); full eval
  runs are marked `eval` and require an explicit opt-in, never blocking CI.
- `scripts/run_tests.sh`: include the harness unit tests; do not run the full
  evaluation matrix.
- Results: JSON per run (pinned model/prompt versions, seeds, N repetitions)
  plus a generated Markdown summary table for the write-up.

## Deferred sub-items

- Public-benchmark subsets (GAIA/tau-bench/SWE-bench) after the Klea-specific
  suite is stable.
- Model-escalation condition (optional multi-model), clearly separated from
  the required single-model condition.
- Human spot-checks and rubric calibration for the LLM judge.
