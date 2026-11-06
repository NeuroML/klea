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

P1 `in progress`, P2-P9 `pending`.

| Phase | Objective | Files under `eval_pkg/` | ADR / design note | Depends on | Exit criterion | Verification | Status |
| --- | --- | --- | --- | --- | --- | --- | --- |
| P1 | CLI case validation command: expose the JSONL loader through `klea-eval`, render `CaseValidationError`, map to exit code 2. | `klea_eval/ui/cli.py`, `klea_eval/errors.py`, `tests/test_cli_validate.py` | ADR-0056, ADR-0060, `agent-evaluation-harness.md` | P0 scaffold (`4c1df875`) | `klea-eval validate <suite>` exits 0 on a valid suite and 2 on an invalid one with a located message; `eval` marker registered. | `pytest eval_pkg -m eval`, `ruff check eval_pkg`, `ty check eval_pkg` | in progress |
| P2 | Run manager and run isolation: per-case run directory, isolated environment, cleanup. | `klea_eval/runmanager.py`, `klea_eval/workspace.py`, `tests/test_runmanager.py` | ADR-0057, D3 | P1 | Each case runs in its own directory under `KLEA_EVAL_WORKSPACE`; directory removed on success and on failure. | `pytest eval_pkg -m eval -k runmanager` | pending |
| P3 | Metrics collector: per-case metrics collected through the runner interfaces. | `klea_eval/metrics.py`, `tests/test_metrics.py` | ADR-0058 | P2 | Runner interface yields metrics for a fixture case; collector aggregates them per case. | `pytest eval_pkg -m eval -k metrics` | pending |
| P4 | Result storage: persist run results in the ADR-0059 reporting format. | `klea_eval/results.py`, `tests/test_results.py` | ADR-0059, D6, D10 | P3 | A completed run is written to the store and read back unchanged, `run_id` stable across reruns. | `pytest eval_pkg -m eval -k results` | pending |
| P5 | Runner adapters: async `httpx` client and SSE parsing against `/query/stream`, backed by hand-written fixtures. | `klea_eval/runners/http.py`, `klea_eval/runners/sse.py`, `tests/test_http_runner.py`, `tests/fixtures/sse/` | D1, D2, ADR-0058 | P4 | Fixture SSE streams parse into ordered events; `localonly` live check passes when a server is present and skips otherwise. | `pytest eval_pkg -m eval -k runner` | pending |
| P6 | Harness pipeline: wire loader, runners and storage behind the `klea-eval` command. | `klea_eval/pipeline.py`, `klea_eval/ui/cli.py`, `tests/test_pipeline.py` | ADR-0060, D1, D4 | P5 | `klea-eval run <suite>` executes a multi-case JSONL suite end to end and stores results. | `pytest eval_pkg -m eval -k pipeline` | pending |
| P7 | Scoring and aggregation: aggregate case scores, suite-level `pass_at_k` from the run index. | `klea_eval/scoring.py`, `tests/test_scoring.py` | ADR-0060, D9, D10 | P6 | Suite score is the mean of case scores; `pass_at_k` matches a hand-computed value on a sample suite. | `pytest eval_pkg -m eval -k scoring` | pending |
| P8 | API and SSE integration: expose runs through the API and the SSE stream. | `klea_eval/api.py`, `klea_eval/ui/cli.py`, `tests/test_api.py` | ADR-0060, D10, D11, `agent-evaluation-harness.md` | P7 | A run is retrievable through the API; retention keeps the last 20 complete runs. | `pytest eval_pkg -m eval -k api` | pending |
| P9 | In-process scheduler, sample suites and CI wiring. | `klea_eval/scheduler.py`, `samples/`, `tests/test_scheduler.py`, `.github/workflows/` | ADR-0060, D7, D8 | P8 | Scheduler runs the sample suites unattended; CI runs the `eval` marked tests. | `pytest eval_pkg -m eval`, CI run | pending |

---

## 4. Commit log

| Phase | Short hash | Subject |
| --- | --- | --- |
| docs | pending | `docs: add eval harness implementation plan` |

Rows are appended as commits land. The phase column is the phase the commit
belongs to, or `docs` for planning-only commits.

---

## 5. How to resume

1. Read this note, then read the phase table for the first row whose status is
   not `done`.
2. Read `devdocs/system/agent-evaluation-harness.md` for the architecture of the
   phase you are picking up, and the ADR it names for the design intent.
3. Check `git log --oneline` and the commit log table above: they should agree.
   If a phase's commits are missing from the table, append them before starting
   new work.
4. Run the phase's verification command before and after the change, so a
   pre-existing failure is not mistaken for a regression.
5. Commit per atomic change, never push, and update the Status column and the
   commit log in the same commit that closes a phase step.
