# Eval harness implementation plan

This note is the implementation tracker for the evaluation harness in `eval_pkg/`.
It sits beside `devdocs/system/agent-evaluation-harness.md`, which holds the
architecture (stage graph, run lifecycle sequence, report shape). This note holds
the delivery plan: the decisions taken before coding, the ordered phases, and the
commit log. Design intent lives in the ADRs; this file records how the work is
being sequenced and how far it has got.

Relevant ADRs:

- ADR-0056: JSONL case definition format and loader (`klea_eval.cases`).
- ADR-0057: run manager and run isolation.
- ADR-0058: metrics collector and runner interfaces.
- ADR-0059: result storage and reporting format.
- ADR-0060: harness pipeline, CLI, scheduler and exit codes.

If this note and an ADR disagree, the ADR wins and this note is corrected.

---

## 1. Ground rules

- One commit per atomic change, **not** one commit per phase. A phase that adds a
  loader, a CLI command and a test file is normally two or three commits.
- Each commit is verified before it is made: `ruff check`, `ruff format --check`,
  `ty check`, and the relevant `pytest` selection. A commit that does not pass
  verification is not made; the change is fixed first.
- Never stage unrelated files. `git status --porcelain` must show only the files
  belonging to the change before `git add`.
- Conventional commit subjects (`feat:`, `fix:`, `test:`, `docs:`, `refactor:`,
  `chore:`), scoped to the package where it helps, e.g.
  `feat(eval): add CLI case validation command`.
- Do not push. The branch is left for the user to review and push.
- ASCII only in code, docs and fixtures: no unicode dashes or arrows.

---

## 2. Decisions

The twelve decisions below were agreed before implementation started. They are
recorded here so later steps do not re-litigate them.

| Id | Decision | Agreed default |
| --- | --- | --- |
| D1 | HTTP transport for the harness client | Async `httpx` against the `/query/stream` endpoint; SSE parsed incrementally, no polling of a blocking endpoint. |
| D2 | How the harness gets SSE responses in tests | Hand-written SSE fixtures checked into the repo; the `localonly` marker gates the checks that need a live server. |
| D3 | Where run workspaces live | `KLEA_EVAL_WORKSPACE` environment variable; default outside the source tree at `~/.local/share/klea-eval`. |
| D4 | Case file layout and fixture resolution | Multi-case JSONL files; a case's fixture base directory is the directory of the JSONL file that owns the case. |
| D5 | Case schema version | Migrate roles in place, keep schema `1.0`. No version bump for the role vocabulary work. |
| D6 | Run identity | Deterministic `run_id` derived from `case_id` plus `harness_seed`; the same inputs give the same id, so reruns are comparable and resumable. |
| D7 | Sample suites | Ship two or three minimal sample suites; enough to exercise the loader and the pipeline, not a benchmark corpus. |
| D8 | Test marking and CI | `eval` marker introduced in P1; CI wiring for the marked tests lands in P9. |
| D9 | LLM judge scoring | Opt-in and out of the first cut. Deterministic metrics only until the pipeline is trusted. |
| D10 | Suite-level pass metric | `pass_at_k` read from the run index, `k=1` by default. |
| D11 | Retention policy | Keep the last 20 complete runs; older complete runs are pruned. |
| D12 | Commit granularity for docs | A docs-only first commit, then per-atomic-change commits thereafter. |

---

## 3. Phases

The phases are ordered; each depends on the ones before it. Every phase names
its exit criterion and the verification command that must pass before its commits
are made.

P1 `done`, P2 `done`, P3 `done`, P4 `done`, P5 `done`, P6 `done`, P7 `done`, P8 `done`, P9 `done`.

| Phase | Objective | Files under `eval_pkg/` | ADR / design note | Depends on | Exit criterion | Verification | Status |
| --- | --- | --- | --- | --- | --- | --- | --- |
| P1 | CLI case validation command: expose the JSONL loader through `klea-eval`, render `CaseValidationError`, map to exit code 2. | `klea_eval/ui/cli.py`, `klea_eval/errors.py`, `tests/test_cli_validate.py` | ADR-0056, ADR-0060, `agent-evaluation-harness.md` | P0 scaffold (`4c1df875`) | `klea-eval validate <suite>` exits 0 on a valid suite and 2 on an invalid one with a located message; `eval` marker registered. | `pytest eval_pkg -m eval`, `ruff check eval_pkg`, `ty check eval_pkg` | done |
| P2 | Run manager and run isolation: per-case run directory, isolated environment, cleanup. | `klea_eval/run_manager.py`, `klea_eval/workspace.py`, `tests/test_run_manager.py` | ADR-0057, D3 | P1 | Each case runs in its own directory under `KLEA_EVAL_WORKSPACE`; directory removed on success and on failure. | `pytest eval_pkg -m eval -k runmanager` | done |
| P3 | Metrics collector: per-case metrics collected through the runner interfaces. | `klea_eval/metrics.py`, `tests/test_metrics.py` | ADR-0058 | P2 | Runner interface yields metrics for a fixture case; collector aggregates them per case. | `pytest eval_pkg -m eval -k metrics` | done |
| P4 | Result storage: persist run results in the ADR-0059 reporting format. | `klea_eval/results.py`, `tests/test_results.py` | ADR-0059, D6, D10 | P3 | A completed run is written to the store and read back unchanged, `run_id` stable across reruns. | `pytest eval_pkg -m eval -k results` | done |
| P5 | Runner adapters: async `httpx` client and SSE parsing against `/query/stream`, backed by hand-written fixtures. | `klea_eval/runners/http.py`, `klea_eval/runners/sse.py`, `tests/test_http_runner.py`, `tests/fixtures/sse/` | D1, D2, ADR-0058 | P4 | Fixture SSE streams parse into ordered events; `localonly` live check passes when a server is present and skips otherwise. | `pytest eval_pkg -m eval -k runner` | done |
| P6 | Harness pipeline: wire loader, runners and storage behind the `klea-eval` command. | `klea_eval/pipeline.py`, `klea_eval/ui/cli.py`, `tests/test_pipeline.py` | ADR-0060, D1, D4 | P5 | `klea-eval run <suite>` executes a multi-case JSONL suite end to end and stores results. | `pytest eval_pkg -m eval -k pipeline` | done |
| P7 | Scoring and aggregation: aggregate case scores, suite-level `pass_at_k` from the run index. | `klea_eval/scoring.py`, `tests/test_scoring.py` | ADR-0060, D9, D10 | P6 | Suite score is the mean of case scores; `pass_at_k` matches a hand-computed value on a sample suite. | `pytest eval_pkg -m eval -k scoring` | done |
| P8 | API and SSE integration: expose runs through the API and the SSE stream. | `klea_eval/api.py`, `klea_eval/ui/cli.py`, `tests/test_api.py` | ADR-0060, D10, D11, `agent-evaluation-harness.md` | P7 | A run is retrievable through the API; retention keeps the last 20 complete runs. | `pytest eval_pkg -m eval -k api` | done |
| P9 | In-process scheduler, sample suites and CI wiring. | `klea_eval/scheduler.py`, `samples/`, `tests/test_scheduler.py`, `.github/workflows/` | ADR-0060, D7, D8 | P8 | Scheduler runs the sample suites unattended; CI runs the `eval` marked tests. | `pytest eval_pkg -m eval`, CI run | done |

---

## 4. Commit log

| Phase | Short hash | Subject |
| --- | --- | --- |
| docs | `8f29ba1e` | `docs(devdocs): add eval harness implementation note` |
| P1 | `550cdf73` | `feat(eval): migrate case roles and resolve fixtures per owning suite file` |
| P1 | `b74871cb` | `feat(eval): add suite validate command` |
| P2 | `535b09d3` | `feat(eval): deterministic run, user and chat identity` |
| P2 | `06530ad7` | `feat(eval): add run workspace layout, creation and atomic publication` |
| P2 | `28167be8` | `feat(eval): add run manager to prepare, execute and publish evaluation runs` |
| P3 | `c2118f27` | `feat(eval): add run metrics collector` |
| P4 | `08a19ec7` | `feat(eval): add result storage writer and reader` |
| P5 | `577fff20` | `feat(eval): add runner adapters for the eval harness` |
| P6 | `95a2d1c5` | `feat(eval): run suites end to end through the pipeline and CLI` |
| P7 | `067b82e4` | `feat(eval): score and aggregate eval runs` |
| P8 | `e137b24a` | `feat(eval): expose stored eval runs through the API with retention` |
| P9 | `97aef208` | `feat(eval): schedule unattended eval suite runs` |
| P9 | `c2b9cf03` | `feat(eval): add sample suites and run eval tests in CI` |

Rows are appended as commits land. The phase column is the phase the commit
belongs to, or `docs` for planning-only commits.

---

## 5. Notes/decisions changed while implementing

Points discovered while delivering P1-P7; later phases must respect them.

- `ty check` must be scoped to `eval_pkg` (`ty check eval_pkg`). Running it over
  the whole repository reports pre-existing errors in other packages, so only
  the eval package is checked before a phase commit.
- The role migration to the ADR-0056 vocabulary required rewriting the existing
  `assistant` turn in `tests/test_cases.py` to an allowed role (`chat`), because
  the loader rejects `assistant`.
- Phase 2 scope caveats: the run manager publishes a run directory without
  `run.json`, index append or retention; those are deferred to later phases.
  Turn execution and scoring are injected callables, so the module stays
  offline and testable without a live server. `start_run` takes `fixture_base`
  explicitly because `TestCase` does not carry its owning suite file. Retries
  use `DEFAULT_MAX_ATTEMPTS = 3` with `RETRY_BACKOFF_SECONDS = 0.5`. An
  already-complete run returns `STATUS_SKIPPED` unless `overwrite=True`. The
  workspace functions take `run_id` first with an optional `root`.
- Phase 3 scope caveats: the metrics collector is a read-only consumer of
  `metrics.jsonl`. The run manager's per-turn lines do not yet carry
  `schema_version` or `record_type`, so the collector infers the record type
  from the payload and defaults the schema version to `1.0`; the final
  `record_type` "run" envelope is still not written, so the run-level values
  are recomputed from the turn lines and `envelope_present` stays false until
  the ADR-0059 writer lands in P4. No price table exists, so `cost_estimate`
  is read from an envelope when present and stays `None` otherwise; cost
  derivation belongs to P7. `MetricsError` and `MetricsSchemaError` live in
  `klea_eval/metrics.py`, following the `WorkspaceError` and
  `RunManagerError` precedent, rather than in `klea_eval/errors.py`. The
  failure line a failed run appends is discarded together with the run
  directory, so the collector parses that shape for `.tmp` inspection only,
  and `pass_at_k` is never produced there: it is a suite-level aggregate of
  the ADR-0060 stage.
- Phase 4 scope caveats: result storage (`klea_eval/results.py`) owns only
  `run.json` and the `runs/index.jsonl` line; directory creation, the
  `COMPLETE` marker and the atomic rename stay in `klea_eval/workspace.py`, and
  the per-turn `metrics.jsonl` lines stay in `klea_eval/run_manager.py`. The run
  manager is not wired to it yet, so `run_case` still publishes a run without
  `run.json` or an index line: P6 does that wiring. Retention (the last 20
  complete runs, D11) is deferred to P8, which owns the exit criterion.
   `transcript.jsonl` writing and `cost_estimate` derivation are still absent,
   belonging to P7; model resolution is implemented instead. A case's
   `run_config.model_overrides` is validated for its shape only at load time (an
   object mapping non-empty role names to non-empty model strings; a role name
   need not be a real one), `--model ROLE=MODEL` on `klea-eval run` supplies
   suite-wide defaults whose value the case's own key wins over, and
   `klea_eval.run_config.apply_run_models` applies the merged map once per run,
   after the run directory is prepared and before the first turn executes: it
   reads the chat's active model map, rejects a requested role the backend does
   not expose, PUTs one credential per provider that has an environment secret,
   POSTs each override with `promote_default` false, and re-reads the active map
   so a role left keyless-credential fails the run. The resolved map is recorded
   as `resolved_models` in `run.json` (`RunMetadata`), in the run's own envelope
   record (`RunMetrics`), in the per-case `CaseScore` and in the `compare_runs`
   row; an override fault records the case as failed, runs no turn and
   publishes nothing. No startup consistency check or rebuild-index command
   exists although ADR-0059 flags the index/directory drift risk.
  `attempts` remains the only per-turn extra; the run manager's turn lines still
  carry no `schema_version` or `record_type`, so only `run.json` and the index
  line are stamped. `ResultsError` and `ResultsSchemaError` live in
  `klea_eval/results.py`, following the `WorkspaceError`, `RunManagerError` and
  `MetricsError` precedent.
- Phase 5 scope caveats: the runner adapters (`klea_eval/runners/sse.py`,
  `klea_eval/runners/http.py`) own SSE framing and the Execute stage's network
  surface only. SSE parsing stays tolerant: comments are skipped, only `data`
  is accumulated, multi-line data is joined and the frame a stream ends
  without a blank line for is still dispatched, while a frame cut before its
  `data` line is dropped; `event`/`id`/`retry` are recognised as framing only.
  `SSEParseError` lives in `klea_eval/runners/sse.py` and `RunnerError`,
  `RunnerHTTPError`, `RunnerStreamError` and `RunnerTimeoutError` live in
  `klea_eval/runners/http.py`, following the `WorkspaceError`,
  `RunManagerError`, `MetricsError` and `ResultsError` precedent rather than
  `klea_eval/errors.py`. Retry and backoff stay in the run manager; the
  adapter only raises typed errors (4xx/5xx to `RunnerHTTPError`, timeout to
  `RunnerTimeoutError`, an `error`/`interrupt` event or a stream without
  `complete` to `RunnerStreamError`). The runner closes only the client it
  created: an injected client or transport belongs to its owner, so tests
  drive an `httpx` stub transport offline and the `localonly` live check gates
  on `KLEA_EVAL_BASE_URL`. `as_turn_executor` bridges to the run manager's
  synchronous `TurnExecutor` through `asyncio.run` and raises when a loop is
  already running; the record it returns carries the usage counters both flat
  and under `usage`, plus `latency_ms`, `tool_calls` and `tool_error_count`.
  `transcript.jsonl` writing, HITL `interrupt` resume, cost derivation and the
   pipeline wiring are still absent, belonging to P6 and P7.
- Phase 6 scope caveats: `klea_eval/pipeline.py` is orchestration only. It
  validates the whole suite through `klea_eval.cases.load_suite` before any run
  starts, resolves the workspace root and runs directory through
  `klea_eval/workspace.py`, derives each run id through
  `klea_eval.identity.derive_identity` so an already complete run is skipped
  before it is prepared, and calls `klea_eval.run_manager.start_run` and
  `run_case` in suite order, single-threaded (the bounded worker pool stays
  deferred). It owns no retry policy, metric parsing, `.tmp` handling or
  transport: those remain in the run manager, the metrics collector and the
  runner adapters. Turn execution and scoring are injected (`execute_turn`,
  `score_turn`), so the module is offline and testable; when neither is given
  the pipeline builds `klea_eval.runners.http.HttpRunner` from `base_url`,
  `endpoint` and `timeout`, and a missing executor with no base URL raises
  `PipelineConfigError`. A failing case is isolated: its failure is recorded in
  its `CaseRun`, no `run.json` and no index line are written for it, and the
  suite continues; only a pre-run fault (an invalid suite, an unusable
  workspace root, a missing executor) aborts the suite before any run. `run.json`
  and the single `runs/index.jsonl` line are written by the pipeline through
  `klea_eval.results.write_metadata` and `append_index_entry` after `run_case`
  has published the run directory, because the run manager still does not write
  them. `SuiteResult.exit_code` is `0` when every case passed and `1` when one
  failed its rubrics; the CLI maps `CaseValidationError` and
  `PipelineConfigError` to exit code `2`. The `klea-eval run` command forwards
  the workspace, endpoint, timeout, harness seed, attempt budget and overwrite
  options, requires a base URL (`--base-url` or `KLEA_EVAL_BASE_URL`) before it
  calls the pipeline, and prints one `<case_id>: pass|fail` line per run.
  Suite-level `pass_at_k`, `transcript.jsonl` writing, HITL `interrupt` resume,
   cost derivation, retention and the scheduler are still absent, belonging to
   P7, P8 and P9.
- Phase 7 scope caveats: `klea_eval/scoring.py` is the ADR-0060 Aggregate stage
  as a read-only module. It reads `runs/index.jsonl` through
  `klea_eval.results.read_index`, groups entries by `case_id` in
  first-index-occurrence order, dedupes by `run_id`, skips non-completed
  entries and loads each selected run through `klea_eval.metrics.load_run`, so
  it never scans run directories and reuses the ADR-0058 per-run envelope
  rather than redefining it. A per-case score is the mean of the numeric rubric
  scores recorded on the run's turn records, falling back to `1.0` for a run
  whose `task_success` is true and `0.0` otherwise; `passed` follows
  `task_success` alone, so a run of unknown success contributes a score but
  never a pass. `suite_score` is the mean of the case scores and `pass_at_k` is
  passed cases over considered cases (`DEFAULT_K = 1`, D10), where only the
  first `k` attempts of a case are inspected. A case whose metrics are missing
  or unreadable is listed in `missing_runs`, counts as failed with score `0.0`
  and never aborts the aggregate; an empty index yields an empty aggregate and
  `k < 1` raises `ScoringError`, which lives in `klea_eval/scoring.py`
  following the `WorkspaceError`, `RunManagerError`, `MetricsError` and
  `ResultsError` precedent. An unsupported index `schema_version` propagates
  `ResultsSchemaError` unchanged. Scoring writes no file, no run directory and
  no index line; `SuiteAggregate.to_dict()` is only the in-memory JSON-ready
  form. Still deferred: LLM-judge rubric scoring and evaluation of the rubric
  `criteria` text (D9), cost derivation (no price table exists, so
  `cost_estimate` is only relayed and summed), `transcript.jsonl` writing, HITL
  `interrupt` resume, retention (P8), the in-process scheduler (P9), a persisted
  suite report artefact, the Markdown reporter and any CLI or API exposure of
  `aggregate_suite` (P8). `pipeline.run_suite` is unchanged and keeps its
  injected `score_turn` hook.
- Phase 8 scope caveats: `klea_eval/api.py` is the read/inspect surface of the
  ADR-0060 Aggregate stage plus the ADR-0059 retention point, and it owns no
  persistence of its own. `get_run` looks the id up through
  `klea_eval.results.read_index` before touching disk, treats a listed run
  without the `COMPLETE` marker (`klea_eval.workspace.is_complete`) as a
  `RunNotFoundError` rather than a result, and reads the envelope and the
  metrics summary through `klea_eval.results.load_stored_run`, so a
  `ResultsSchemaError` propagates unchanged while any other results fault is
  translated to `ApiError`. `list_runs` reads the index only and never opens a
  run directory. `suite_summary` is a thin delegation to
  `klea_eval.scoring.aggregate_suite`, which closes the P7 deferred item (CLI
  or API exposure of `aggregate_suite`) without redefining a score. Retention
  (`apply_retention`, D11, `DEFAULT_RETENTION = 20`) keeps the most recent 20
  completed runs per case, groups in first-index-occurrence order, purges the
  whole directory of an older completed run with `shutil.rmtree` and rewrites
  `runs/index.jsonl` atomically through a fsynced sibling plus `os.replace`,
  preserving file order; non-completed index lines and leftover `.tmp`
  directories are neither counted nor deleted, and `keep < 1` or a bool raises
  `ApiError`. `ApiError` and `RunNotFoundError` live in `klea_eval/api.py`,
  following the `WorkspaceError`, `RunManagerError`, `MetricsError`,
  `ResultsError` and `ScoringError` precedent. Retention is applied from the
  CLI (`klea-eval prune`) and is not wired into `pipeline.run_suite`.
  `klea-eval show`, `runs`, `prune` and `summary` were added beside the
  existing commands, keeping the heavy imports deferred. "SSE integration"
  here means reading stored runs: no web/ASGI server or served SSE endpoint
  exists, and `eval_pkg` stays a library plus CLI. Still deferred: partial
  retention keeping only `run.json` plus `metrics.jsonl` beyond the window (a
  purged run loses its whole directory), the automatic post-publish retention
  hook, a startup index/directory consistency check or rebuild-index command,
  `transcript.jsonl` writing, HITL `interrupt` resume, cost derivation (no
  price table exists), the bounded worker pool (P9), MLflow export and the
  Markdown reporter.
- Phase 9 scope caveats: `klea_eval/scheduler.py` is only the ADR-0060 Schedule
  stage. It enumerates suites (`discover_suites`: a JSONL file, or a directory's
  `*.jsonl` files sorted by name), drives each through
  `klea_eval.pipeline.run_suite` and aggregates each suite's stored runs through
  `klea_eval.scoring.aggregate_suite`, restricted to that suite's `case_id`s, so
  no loading, execution, storage or scoring logic is redefined. Execution stays
  sequential (`DEFAULT_MAX_WORKERS = 1`; the bounded worker pool is deferred). A
  suite that cannot start is isolated in its `SuiteRun.error` and the remaining
  suites still run; only an unusable top-level argument raises
  `SchedulerConfigError`, which lives in `klea_eval/scheduler.py` following the
  `WorkspaceError`, `RunManagerError`, `MetricsError`, `ResultsError`,
  `ScoringError` and `ApiError` precedent. `find_incomplete_runs` reports a
  leftover `runs/<run_id>.tmp/` (or unpublished final) directory as an
  interrupted run and never treats it as a result. `ScheduleReport.exit_code`
  maps the ADR-0060 codes: `0` every case passed, `1` a case failed its rubrics,
  `2` a suite could not start (it outranks everything else because nothing was
  stored for that suite), `3` incomplete `.tmp` runs were found. The D7 samples
  are three minimal ASCII suites under `eval_pkg/samples/` (`smoke.jsonl`,
  `multi_turn.jsonl`, `multi_case.jsonl`) with no fixtures: enough to exercise
  the loader, the pipeline and per-case aggregation, not a benchmark corpus. The
  D8 CI wiring is one `Run eval-marked tests` step in
  `.github/workflows/ci.yml` running `pytest -q -m eval` inside `eval_pkg`, after
  the unit tests and before the ruff lint; the `eval` marker itself was already
  registered in `eval_pkg/pyproject.toml`, so no marker change was needed.
  Still deferred: the bounded worker pool and any parallel worker count, an
  external or broker-backed scheduler backend, a `klea-eval schedule` CLI
  command, automatic repair or redo of leftover `.tmp` runs,
  `transcript.jsonl` writing, HITL `interrupt` resume, cost derivation (no price
  table exists), MLflow export, a persisted per-suite report artefact and the
   Markdown reporter.

### Post-P9 refinements

Landed after P9. Where a caveat above and a point below disagree, the point
below is current.

- Run identity (refines D6): `klea_eval.identity.derive_identity` folds the
  attempt index, the case revision, the requested per-role model map
  (`model_overrides`), the resolved model map (`resolved_models`) and the
  request endpoint into the derived `run_id`, so two runs of a case differ
  whenever their attempt, revision, requested role models, resolved models or
  endpoint differ, while identical inputs still give a comparable, resumable
  id.
- Publication order: `run.json` is written before the `COMPLETE` marker is set
  and before the `runs/index.jsonl` line is appended, so a directory that
  carries the marker always carries its metadata.
- Re-indexing: a run directory that carries the `COMPLETE` marker but has no
  `runs/index.jsonl` line is re-indexed from its stored artifacts instead of
  being re-run.
- Overwrite safety: an overwriting run keeps the previous run directory and its
  index line until the new run has published successfully. The previous
  directory is held as a superseded copy and restored if the final rename
  fails, so a failed overwrite leaves the earlier result intact.
- Stream completion: a `complete` event whose payload is malformed or truncated
  fails the case; it is not accepted as a clean finish.
- Ungraded runs: a run with no grade is counted in the suite aggregate's
  `ungraded` counter and labelled `ungraded` in the report, while its stored
  status stays `completed` and it does not change the exit code (still `0` when
  every case passed, `1` when a case failed its rubrics).
- Per-case cwd: each case's turn carries its isolated run directory as
  `client_context.cwd`, so the agent works inside the case workspace rather
  than in the server's working directory.
- Workspace precedence: the workspace root resolves `--workspace`, then
  `KLEA_EVAL_WORKSPACE`, then `.`, taking the first that is set.
- `klea-eval report`: added beside `validate`, `run`, `show`, `runs`, `prune`
  and `summary`; it writes the stored suite results to an output path through
  the `write_report(results, output)` writer.

---


## 6. How to resume

1. Read this note, then read the phase table: every row (P1 to P9) is `done`
   and no atomic change remains. The deterministic
   identity module (`klea_eval/identity.py`, commit `535b09d3`), the workspace
   module (`klea_eval/workspace.py`, commit `06530ad7`), the run manager
   (`klea_eval/run_manager.py`, commit `28167be8`), the metrics collector
   (`klea_eval/metrics.py`, commit `c2118f27`), result storage
   (`klea_eval/results.py`, commit `08a19ec7`), the runner adapters
   (`klea_eval/runners/http.py` and `klea_eval/runners/sse.py` with
   `tests/test_http_runner.py` and `tests/fixtures/sse/`, commit `577fff20`),
   the harness pipeline (`klea_eval/pipeline.py`, `klea_eval/ui/cli.py` and
   `tests/test_pipeline.py`, commit `95a2d1c5`), scoring and aggregation
   (`klea_eval/scoring.py` and `tests/test_scoring.py`, commit `067b82e4`), the
   run API and retention (`klea_eval/api.py`, `klea_eval/ui/cli.py` and
   `tests/test_api.py`, commit `e137b24a`) and the in-process scheduler, sample
   suites and CI wiring (`klea_eval/scheduler.py`, `samples/`,
   `tests/test_scheduler.py` and `.github/workflows/ci.yml`, commits `97aef208`
   and `c2b9cf03`) are delivered.
2. Read `devdocs/system/agent-evaluation-harness.md` for the architecture of the
   phase you are picking up, and the ADR it names for the design intent. The run
   and validate commands are in `devdocs/system/eval-harness-run-guide.md`.
3. Check `git log --oneline` and the commit log table above: they should agree.
   If a phase's commits are missing from the table, append them before starting
   new work.
4. Run the phase's verification command before and after the change, so a
   pre-existing failure is not mistaken for a regression.
5. Commit per atomic change, never push, and update the Status column and the
   commit log in the same commit that closes a phase step.
