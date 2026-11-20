# AGENTS.md - eval_pkg

Guidance for agents working inside the `eval_pkg` package.

## Purpose

`eval_pkg` is the evaluation harness for the architecture decision records
0056 through 0060. It provides the fixtures, runners and reporting helpers
used to evaluate those ADRs reproducibly, and it exists so that every
reviewer runs the same evaluation the same way.

## Governing ADRs

- ADR 0056 - evaluation harness scope
- ADR 0057 - fixture and data handling
- ADR 0058 - runner interfaces
- ADR 0059 - result reporting format
- ADR 0060 - reproducibility and pinned environments

Changes to this package must stay consistent with the ADRs above.

## Layout

- `eval_pkg/__init__.py` - package entry point and public exports.
- `eval_pkg/runners/` - evaluation runners that drive a single scenario.
- `eval_pkg/fixtures/` - fixture loaders and the fixture data used by runs.
- `eval_pkg/reporting/` - result collection and report rendering.
- `tests/` - unit and integration tests for this package.
- `AGENTS.md` - this file.

## Commands

Run from the repository root.

- Install (editable, with dev extras): `pip install -e ".[dev]"`
- Test: `pytest eval_pkg`
- Lint: `ruff check eval_pkg`
- Type check: `ty check eval_pkg`

## Conventions

- Keep all text in this package ASCII-only.
- Add a test next to any new runner or reporting behavior.
- Record which ADR a change serves in the change description.
