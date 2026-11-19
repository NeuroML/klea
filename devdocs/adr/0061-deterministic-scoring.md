---
status: accepted
date: 2026-11-18
decision-makers: [Architecture Team]
---

# ADR-0061: Deterministic-only scoring

## Context and Problem Statement

The evaluation harness scores a run in the ADR-0060 Score stage and records the
verdict through `record_evaluation_result(score, pass_fail)`. That verdict flows
straight into `task_success = all(pass_fail)`, and `task_success` is what
`klea_eval.labels.classify_case`, the suite `exit_code`, `suite_score` and
`pass_at_k` consume. Scored fields are therefore not cosmetic: they decide the
reported pass/fail of a run, of a case and of a suite, plus the process exit
code an operator or CI job branches on.

A case (ADR-0056) may declare rubrics, and a rubric may carry free-text
`criteria`. Free prose cannot be scored by a machine check, so a
rubric-declaring case can complete with no grade at all, which is surfaced as
the `grade-missing` label and exit code `4`.

The obvious remedy is an LLM judge. The question this ADR settles is: how
should the harness grade cases whose criteria are not machine-checkable - with
a judged verdict, or with deterministic checks only?

## Decision Drivers

* Reproducibility of graded fields: `run.json` and the versioned
  `metrics.jsonl` (ADR-0058) are re-readable offline, and a verdict that cannot
  be reproduced from those artifacts breaks that guarantee.
* Variance control: turn content and latency are already stochastic, so adding
  a second, independent variance source (judge identity and its sampling) makes
  a score change between identical reruns of the same suite.
* Exit-code and label integrity: `classify_case`, `exit_code`, `suite_score`
  and `pass_at_k` must mean one thing, so a model-scored pass/fail must never be
  indistinguishable from a machine-checked one.
* Determinism of the ADR-0060 stages: Load, Schedule, Execute, Score, Persist
  and Aggregate should stay reproducible where they can be.
* Offline operation: the harness must not need a judge service, credential or
  availability window to grade a suite.

## Considered Options

* LLM judge gating scoring (opt-in, out of the first cut, as recorded in D9).
* Deterministic-only scoring (chosen).
* Hybrid: judge happy to advise, but never allowed to gate.

## Decision Outcome

Chosen option: "Deterministic-only scoring", because it is the only option that
keeps the graded fields reproducible from stored artifacts and keeps
`task_success`, the labels and the exit codes meaningful, which is the k.o.
criterion above.

The LLM judge is rejected for gating. Deterministic checks are the only grading
path: a run is graded by machine checks over its `expected_outputs` and its
`tool_expectations` (ADR-0056), never by a model verdict. Those checks run in
the Execute -> Score stage of the pipeline (ADR-0060), and each per-check
verdict is recorded in the run record as `metrics.expectation_checks` beside the
derived `task_success`, so `score_run` grades a run from its recorded verdicts
alone and the grade stays reproducible without re-reading the run workspace. Any future judged
scoring would need opt-in, a pinned and recorded judge model id in `run.json`,
cached verdicts so a rerun reuses the same score, and strict non-gating so a
judged number never reaches `task_success`; that is a new decision and needs its
own ADR.

### Consequences

* Good, because a graded field is reproducible from `run.json` and
  `metrics.jsonl` (ADR-0058) alone, with no judge service to reach.
* Good, because `classify_case`, `exit_code`, `suite_score` and `pass_at_k`
  carry a single, deterministic meaning.
* Good, because grading adds no variance beyond the turn content the harness
  cannot control anyway.
* Bad, because scoring depth must come from `expected_outputs` and
  `tool_expectations`, so a case whose quality lives only in prose `criteria` is
  harder to grade and may end `grade-missing`.
* Bad, because exit code `4` now narrows to a rubric-declaring suite whose
  deterministic checks produce no grade, which is a narrower and more actionable
  state than a general judge-unavailable fallback.

### Confirmation

The decision is confirmed when the harness contains no judged scoring path: the
`Scorer` applies deterministic checks only, `record_evaluation_result` receives
verdicts from machine checks alone, and no judge protocol, client or
configuration key exists. The `grade-missing` label and exit code `4` remain the
only outcome for a rubric-declaring case that its deterministic checks cannot
grade.

## Pros and Cons of the Options

### LLM judge gating scoring

An external or local model grades the turn against the rubric and its verdict
enters `record_evaluation_result(score, pass_fail)`.

* Good, because free-text `criteria` could be scored without rewriting the case.
* Neutral, because it needs a judge service and credentials.
* Bad, because a judged pass/fail is model-dependent and cannot be told apart
  from a machine-checked one, so the labels, exit codes and derived metrics
  become model-dependent too.
* Bad, because it adds judge identity and sampling as a second variance source,
  so identical reruns stop being comparable.
* Bad, because it breaks offline reproducibility of a stored run.

### Deterministic-only scoring

Machine checks over `expected_outputs` and `tool_expectations` are the sole
grading path.

* Good, because the verdict is reproducible from the stored run.
* Good, because grading needs no external service and works offline.
* Good, because the exit codes and labels keep a single meaning.
* Bad, because free-text `criteria` stays ungraded unless it is expressed as a
  machine-checkable check.

### Hybrid: judge advises, never gates

A judged score is recorded as an advisory number while the gate stays
deterministic.

* Good, because it preserves a deterministic gate.
* Neutral, because it still needs a judge service to produce the advisory value.
* Bad, because an advisory number invites misuse, and the harness would carry
  the judge plumbing for a field nothing depends on.

## More Information

This decision sits with the surrounding harness contracts: the case format and
the fixture resolution rule (ADR-0056), the versioned per-run `metrics.jsonl`
envelope (ADR-0058) and the six-stage pipeline whose Score stage it constrains
(ADR-0060). It also closes the scoring question left open by D9 in the
implementation notes.

The following are open design questions, recorded as follow-ups, not decisions:

* What machine-checkable form should a rubric `criteria` take, so that a case
  can express its intent without free prose?
* What pass threshold should a scale rubric use when it is expressed as a
  machine check?

These can be revisited as the harness matures. Judged scoring itself is not
reopened by them; it stays rejected unless a future ADR reverses it.
