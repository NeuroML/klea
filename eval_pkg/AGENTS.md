# AGENTS.md - eval_pkg

Guidance for agents working inside the `eval_pkg` package.

## Purpose

`eval_pkg` is the evaluation harness for Klea. It provides the fixtures, runners
and reporting helpers used to evaluate Klea reproducibly, and it exists so that
every reviewer runs the same evaluation the same way.

`eval_pkg` (import name `klea_eval`) is the Klea-specific evaluation harness:
it exists to evaluate Klea and is not a general-purpose evaluation harness or
benchmarking platform.  Every suite, fixture and check targets Klea's own
agent or RAG server.

## Governing ADRs

- ADR 0056 - Test Case Storage Format
- ADR 0057 - Run Manager & Isolation
- ADR 0058 - Metrics Collection & Aggregation
- ADR 0059 - Result Storage
- ADR 0060 - Harness Execution & Orchestration
- ADR 0061 - Deterministic-only scoring
- ADR 0062 - Klea-only evaluation harness scope exclusions

Changes to this package must stay consistent with the ADRs above.

## Layout

- `eval_pkg/klea_eval/__init__.py` - package entry point and public exports.
- `eval_pkg/klea_eval/runners/` - evaluation runners that drive a single scenario.
- `eval_pkg/tests/fixtures/` - fixture data used by the harness tests.
- `eval_pkg/klea_eval/reporting.py` - result collection and report rendering.
- `tests/` - unit and integration tests for this package.
- `AGENTS.md` - this file.

## Commands

Run from the repository root.

- Install (editable, with dev extras): `uv pip install -e "./eval_pkg[dev]"`
- Test: `pytest eval_pkg`
- Lint: `ruff check eval_pkg`
- Type check: `ty check eval_pkg`

## Conventions

- Keep all text in this package ASCII-only.
- Add a test next to any new runner or reporting behavior.
- Record which ADR a change serves in the change description.
