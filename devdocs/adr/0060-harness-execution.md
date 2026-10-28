---
status: "proposed"
date: 2026-10-28
decision-makers: [Architecture Team]
---

# ADR-0060: Harness Execution & Orchestration

## Context and Problem Statement

ADR-0059 (Result Storage) closes with an explicit deferral: "Harness orchestration, the CLI surface and run scheduling are out of scope here and belong to a separate, later decision" (`0059:131`). The four preceding decisions therefore define contracts but no driver for them:

- **ADR-0056 (Test Case Storage Format)**: test cases are JSONL lines keyed by `case_id`, validated at load time, immutable and schema-versioned.
- **ADR-0057 (Run Manager & Isolation)**: a run is a `RunContext` with a deterministic UUID v5 `run_id` (derived from `case_id` + harness seed), an isolated per-run workspace, eagerly resolved role-to-model mappings, and persistence as the final lifecycle step.
- **ADR-0058 (Metrics Collection & Aggregation)**: metrics are extracted primarily from the agent's endpoints (SSE during execution, JSON on run completion) and funnelled through one `RunMetricsCollector` per run (`record_tool_call`, `record_turn_metrics`, `record_evaluation_result`, `finalize`).
- **ADR-0059 (Result Storage)**: a completed run is the directory `runs/<run_id>/` (`run.json`, `metrics.jsonl`, `transcript.jsonl`, `artifacts/`, `COMPLETE`) plus one appended line in `runs/index.jsonl`.

Nothing yet enumerates cases, schedules runs, drives the conversation against the agent, invokes the scorer, hands results to the storage writer, or aggregates a suite. `devdocs/backlog.md` tracks this gap as the unimplemented `eval_pkg` harness (`:110`) and as the owner of the deferred tier-2 API/orchestrator scenarios (`:408-410`) and the retargeting of the CLI-based `agent_pkg/e2e/` suite to the API/SSE path (`:400-402`).

The question: what is the top-level execution component that turns a JSONL suite into stored runs, and where are its boundaries?

## Decision Drivers

* **Composition over redefinition**: the execution layer must consume the accepted interfaces of ADR-0056/0057/0058/0059 verbatim and must not re-specify the case format, run identity, metrics schema or storage layout.
* **Agent-agnostic execution**: ADR-0058 collects metrics from endpoints, so the harness must drive the agent over its API/SSE surface rather than importing Klea internals; third-party agents exposing the same surface must be evaluable.
* **Determinism and reproducibility**: the same suite + seed must produce the same `run_id`s, the same model resolution and the same stored metrics (ADR-0057 invariants).
* **Durability and crash recovery**: per-turn metrics must already be on disk when the process dies, and an interrupted run must be distinguishable from a completed one (ADR-0058 lazy streaming, ADR-0059 `COMPLETE` marker).
* **Bounded local concurrency**: runs must be able to execute in parallel, but only within local resource limits, and without external infrastructure.
* **Operational simplicity**: one command must be enough to run a suite; no databases, brokers or services (ADR-0057 driver), only standard filesystem and JSON libraries.
* **Inspectability (ADR-0013)**: progress, per-run status and failures must be human-readable while the suite runs and afterwards.
* **Clear failure semantics**: a misconfigured suite (bad case, unresolvable role, unknown schema version) must fail differently from a suite whose cases legitimately fail their rubrics, and both differently from an interrupted run.
* **Extensibility**: new stages (extra scorers, MLflow export, a different scheduler backend) must be addable without touching the core loop.

## Considered Options

1. **A dedicated `eval_pkg` harness package with a stage pipeline (load cases -> schedule runs -> execute turns over the API/SSE endpoint -> score -> persist -> aggregate) behind a single CLI entrypoint.**
2. **Extend the existing CLI-based `agent_pkg/e2e/` suite instead of adding a package.**
3. **In-process/library execution: import the orchestrator and drive it directly from the harness.**
4. **Shell/Make glue script: a case loop, HTTP calls and `jq` aggregation.**

A distributed worker or broker-backed queue is deliberately *not* listed: it cannot replace the harness, only the backend of its Schedule stage, and is recorded as a sub-decision under Decision Outcome.

## Decision Outcome

**Chosen option: "A dedicated `eval_pkg` harness package with a stage pipeline (load cases -> schedule runs -> execute turns over the API/SSE endpoint -> score -> persist -> aggregate) behind a single CLI entrypoint"**, because it is the only option that composes the already-accepted interfaces -- JSONL case loading (ADR-0056), `RunContext`/`run_id` (ADR-0057), endpoint-based `RunMetricsCollector` extraction (ADR-0058) and the `runs/<run_id>/` + `index.jsonl` writer (ADR-0059) -- without duplicating any of them. The harness itself is required whichever backend runs it; which backend runs the Schedule stage is a separate sub-decision below, defaulting to an in-process bounded worker pool and adding no infrastructure beyond the filesystem.

### Pipeline stages

Each stage has one input contract and one output contract; stages are separable so that each can be tested against a fake of its neighbour.

1. **Load.** Read the suite JSONL line by line (ADR-0056), validate each case (schema version, required fields, turn ordering, `case_id` uniqueness across the suite) and reject the suite before any run starts if validation fails.
2. **Schedule.** For each case, derive the `run_id` (UUID v5 over `case_id` + seed), resolve roles eagerly (ADR-0057) and create the `RunContext` and isolated workspace. The scheduler emits runs into a bounded local worker pool.
3. **Execute.** Drive the run's turns against the agent endpoint: send the turn, consume the SSE stream, and feed `record_tool_call` / `record_turn_metrics` as events arrive. Turn content is persisted to `transcript.jsonl` as it is produced.
4. **Score.** Run the case's rubrics against the executed turns (LLM-as-judge or deterministic checks, per ADR-0056 rubric types) and feed `record_evaluation_result`.
5. **Persist.** Call `RunMetricsCollector.finalize()`, then hand the run to the ADR-0059 writer: write into `runs/<run_id>.tmp/`, flush and `fsync`, write `COMPLETE` last, rename to `runs/<run_id>/`, and append one line to `runs/index.jsonl`.
6. **Aggregate & report.** Read `runs/index.jsonl` (never the run directories) to produce the suite summary -- per-case pass/fail, pass@k, token totals and cost -- and to print the run listing.

### Scheduler backend (sub-decision)

A distributed worker/queue is **not** an alternative to this ADR: load, execute, score, persist and aggregate have to exist regardless of what runs the Schedule stage, so only that stage's backend is variable. No off-the-shelf evaluator can simply be configured here either - the case format (ADR-0056), run identity (ADR-0057), metrics schema (ADR-0058) and storage layout (ADR-0059) are project-specific contracts a third-party harness would have to be modified to honour, which is at least the work of this thin pipeline. Because each stage has one input and one output contract, the backend is separable:

* **Chosen now: in-process bounded worker pool.** Stage 2 hands work to at most N workers in one process; no service to operate, one command runs a suite, satisfying ADR-0057's no-external-infrastructure driver.
* **Deferred: external worker/scheduler library or broker-backed queue.** Adoptable later behind the same stage boundary without touching load, execute, score or persist; rejected for now because it adds broker operation, credentials and availability failure modes to every run and breaks local reproducibility (ADR-0057).
* **Revisit when** one suite's throughput no longer fits the local worker count, or runs must span machines/CI runners.

### Execution semantics

- **Local, bounded concurrency.** Execution is sequential by default; a worker count enables parallel runs. Nothing in execute, score or persist assumes a broker: the default backend is a bounded local worker pool, and a broker-backed backend is deferred by the Scheduler backend sub-decision.
- **One workspace per run, owned by one process.** A run is written by exactly one worker; no two workers write the same run directory, so the ADR-0059 rename-at-completion protocol needs no cross-process locking.
- **Determinism.** Case order, `run_id` derivation, model resolution and rubric evaluation ordering are deterministic given the suite and seed; concurrency affects only scheduling, never the stored content of a run.
- **Crash recovery.** Because per-turn metrics stream to `metrics.jsonl` and the directory is renamed only at completion, a killed harness leaves `runs/<run_id>.tmp/` directories that are unambiguously incomplete; the scheduler reports them and never treats them as results.
- **Idempotent re-runs.** Re-running a suite with the same seed recomputes the same `run_id`s; an existing `COMPLETE` run is skipped unless overwrite is requested, and a leftover `.tmp` is discarded and redone.

### Failure semantics

Exit codes separate the three failure classes so CI can react correctly:

- **0** -- the suite completed and every case met its rubrics.
- **1** -- the suite completed but one or more cases failed their rubrics (the results are valid and stored).
- **2** -- the suite could not start: invalid case, duplicate `case_id`, unknown schema version, unresolvable role, or unwritable run root. Nothing is stored.
- **3** -- execution was interrupted, or incomplete `.tmp` runs were found and not repaired.

A single run failing mid-execution is not a suite-level error: its failure is recorded in `run.json`/metrics, the index line is written, and the suite continues.

### Scope boundaries

This ADR decides only the execution layer. It does not change the case format (ADR-0056), run identity or isolation (ADR-0057), the `RunMetrics` schema (ADR-0058) or the on-disk layout (ADR-0059). Human-in-the-loop review mid-plan, MLflow export and browser-level UI testing remain separate concerns.

### Consequences

* Good, because the harness is a thin composition of four accepted decisions: it adds orchestration and nothing else, so changes to case format, metrics or storage do not require changes here.
* Good, because endpoint-based execution keeps the harness agent-agnostic: the same loop evaluates Klea and any third-party agent that exposes the API/SSE surface.
* Good, because the pipeline is inspectable both live (per-turn streaming, progress on screen) and afterwards (plain-text run directories plus a one-line-per-run index), and deterministic re-runs reproduce the same results.
* Good, because exit codes distinguish configuration errors from evaluation failures, so CI fails for the right reason.
* Bad, because it is a new top-level package with its own tests, CLI surface and documentation, and the stage boundaries add indirection for what could be a single script at small scale.
* Bad, because endpoint-only execution loses anything the agent does not expose over its API: internal state transitions and retries are invisible unless the agent reports them (ADR-0058 secondary hooks), and a non-compliant agent yields no token or cost metrics.
* Bad, because the default in-process backend uses one machine inefficiently for very large suites (the ceiling is the worker count, not the cluster); that is why the backend is a separable sub-decision, and it is not a judgement against the harness itself.
* Bad, because streaming metrics plus rename-at-completion makes the harness responsible for correct `fsync`/rename ordering; a bug there produces runs that look complete but are truncated.

### Confirmation

* Unit tests for the loader: unknown `schema_version`, duplicate `case_id`, non-sequential turns and absolute fixture paths are rejected before any run starts (exit code 2).
* Unit tests for the scheduler: the same suite + seed yields the same `run_id` set and the same model resolution; a completed `COMPLETE` run is skipped, a leftover `.tmp` is redone.
* A fake agent endpoint serving a recorded SSE stream: the harness feeds `record_tool_call`/`record_turn_metrics` per event, and the finalized `metrics.jsonl` matches the ADR-0058 example record.
* A crash test: killing the harness mid-run leaves `runs/<run_id>.tmp/` with `metrics.jsonl` present and no `COMPLETE` marker, and the reader/index never counts it as a result.
* An end-to-end suite run asserting exit codes 0, 1 and 2 for all-pass, case-failure and invalid-suite inputs, and that `runs/index.jsonl` has exactly one line per completed run.
* The first concrete consumer: the ADR-0041 measurement case for independent steps (`devdocs/backlog.md:37-39`), which this harness is what finally makes measurable.

## Pros and Cons of the Options

### Option 1: Dedicated `eval_pkg` stage pipeline behind a CLI (chosen)

* Good, because it reuses the JSONL case loader (ADR-0056), `RunContext` (ADR-0057), `RunMetricsCollector` (ADR-0058) and the storage writer (ADR-0059) as-is, adding no competing definition of any of them.
* Good, because endpoint/SSE execution keeps the harness decoupled from Klea internals and makes third-party agent evaluation possible.
* Good, because the stages are individually testable against fakes, and new stages (extra scorers, MLflow export) slot in without touching the core loop.
* Good, because one command runs a suite, and deterministic `run_id`s make re-runs idempotent and results comparable.
* Neutral, because the default in-process pool is sufficient at current scale; a distributed backend is a separate sub-decision.
* Bad, because it is a new package to build, test and document rather than an extension of something that already runs.

### Option 2: Extend the CLI-based `agent_pkg/e2e/` suite

* Good, because the scenarios and checks already exist and would not need rewriting.
* Good, because it requires no new package or entrypoint.
* Bad, because it is CLI-coupled: the `cli` subcommand was removed and the suite already needs retargeting to the API/SSE path (`devdocs/backlog.md:400-402`).
* Bad, because it expresses checks as pytest assertions, not as per-run `RunMetrics`, artifacts and index lines -- the very outputs ADR-0058/0059 define.
* Bad, because the backlog assigns tier-2 API/orchestrator scenarios to `eval_pkg` (`devdocs/backlog.md:408-410`), so extending the e2e suite would duplicate the intended home.

### Option 3: In-process/library execution (import the orchestrator and drive it directly)

* Good, because in-process calls are the fastest and simplest path, with no HTTP or serialization boundary.
* Good, because it gives full access to internal state, including metrics an endpoint would not expose.
* Bad, because it contradicts ADR-0058's agent-agnostic, endpoint-based primary collection and its third-party evaluation strategy: a third-party agent cannot be imported.
* Bad, because it couples the harness to Klea internals, so internal refactors break the harness and the harness cannot fairly compare agents.
* Bad, because there is no SSE stream to parse, so the event-driven collection points ADR-0058 relies on do not exist.

### Option 4: Shell/Make glue script

* Good, because it is small, dependency-free and easy to read at first glance.
* Good, because it needs no Python package or test suite of its own.
* Bad, because it has no determinism discipline: seeding, `run_id` derivation and resolution order would be re-implemented ad hoc in shell.
* Bad, because it offers no retry, timeout or partial-failure semantics and no bounded concurrency.
* Bad, because `jq`-based aggregation would fork from the `index.jsonl` contract and drift from the Python readers.

*Scheduler backends (in-process bounded worker pool versus an external worker/scheduler library or broker-backed queue) are not harness alternatives and therefore carry no option heading here; their trade-offs are weighed in the "Scheduler backend (sub-decision)" subsection under Decision Outcome.*

## More Information

No harness implementation exists yet: the only references to `RunMetricsCollector` in the repository are in ADRs 0057, 0058 and 0059, so everything described above is a planned design rather than a description of existing code. Implementation should therefore be framed as new work in `eval_pkg`, built against the accepted contracts of the four related ADRs, and should land in increments (loader + scheduler first, execute/score next, persist + aggregate last) so that each stage can be confirmed by the tests listed under Confirmation. The open items this ADR does not settle -- mid-plan human-in-the-loop review (ADR-0046), optional MLflow export and browser-level UI testing -- remain with their own decisions and the backlog.

### Related Decisions

- **ADR-0056 (Test Case Storage Format)**: supplies the JSONL suite the harness loads and the `case_id` it keys runs by; validation rules are enforced by the loader, not redefined here.
- **ADR-0057 (Run Manager & Isolation)**: supplies `RunContext`, deterministic `run_id` derivation, per-run workspaces and eager model resolution; the scheduler instantiates them and places persistence last, as that ADR requires.
- **ADR-0058 (Metrics Collection & Aggregation)**: supplies the `RunMetricsCollector` the execute and score stages feed, and the endpoint-based collection strategy the execution loop implements.
- **ADR-0059 (Result Storage)**: supplies the write protocol and layout the persist stage performs and the `index.jsonl` the aggregate stage reads; this ADR fills the orchestration it explicitly deferred (`0059:131`).
- **ADR-0041 (Plan step granularity, explicit dependencies and parallel step execution)**: its open "Measurement" item is the first concrete consumer of this harness; the measurement is a motivation for building it, not a dependency of its design.

### References

- `devdocs/backlog.md`: the `eval_pkg` harness entry (`:110`), the ADR-0041 measurement case (`:37-39`), e2e retargeting to the API/SSE path (`:400-402`) and tier-2 orchestrator scenarios (`:408-410`).
- [JSONL Format Specification](https://jsonlines.org/)
- [Server-Sent Events (WHATWG HTML Living Standard)](https://html.spec.whatwg.org/multipage/server-sent-events.html)
- [MADR: Markdown Any Decision Records](https://adr.github.io/madr/)
