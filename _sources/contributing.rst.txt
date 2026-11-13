Contributing
============

Development setup
-----------------

Requirements: Python 3.12 or later and ``uv`` (``pip`` works but ``uv``
is preferred -- see :doc:`install` for extras and PyTorch notes).  To
detect whether a venv is ``uv``-managed, look for ``uv = <version>`` in
``<venv>/pyvenv.cfg`` (``uv`` writes it on ``uv venv``; ``python -m
venv`` does not).

Clone the repository and install in editable mode::

   git clone https://github.com/NeuroML/klea.git
   cd klea
   uv pip install -r requirements-dev.txt

Use ``uv`` for all package operations when it is available; do not use
``pip``.  Each ``*_pkg/`` is a separate installable (``setup.cfg``);
see ``AGENTS.md`` for per-package ``uv pip install -e .`` notes.

Building the docs locally needs ``sphinxcontrib-typer`` from
``requirements-docs.txt`` -- without it ``make html`` falls back to the
system sphinx and the ``.. typer::`` CLI reference pages render empty::

   uv pip install -r requirements-docs.txt
   cd docs && make html   # -> docs/_build/html

Workflow
--------

This project uses a standard fork-and-PR workflow:

1. Fork the repository on GitHub.
2. Create a feature branch from ``development``.
3. Make your changes and run verification (lint, typecheck, test).
4. Submit a pull request targeting the ``development`` branch.
5. Address review feedback if any.

Only pick up issues tagged with the ``help wanted`` label
(https://github.com/NeuroML/klea/issues?q=is%3Aissue+state%3Aopen+label%3A%22help+wanted%22);
other issues are planned or internal work.  Before starting on an issue,
comment on it first to check its status, and keep any discussion on the
issue itself so it remains the single place for discussion.

All contributors are acknowledged via the all-contributors specification
(https://allcontributors.org/), so contributions of any kind are welcome
and tracked on the project ``Readme``.

Commands
--------

This repository uses an incremental, review-driven workflow (see
``AGENTS.md``): apply one logical change, run the relevant verification
below, present the ``git diff`` for review, and wait for approval before
the next step.  Verification covers lint, typecheck, relevant tests, and
``--help`` for any CLI you touched.

Lint and format
~~~~~~~~~~~~~~~

.. code-block:: bash

   ruff check . --fix          # in the affected package
   ruff format .
   ruff check . --select I --fix   # import sorting

Type check
~~~~~~~~~~

.. code-block:: bash

   ty                          # from the repository root; extra-paths for all packages are in ty.toml

Run tests
~~~~~~~~~

.. code-block:: bash

   # All tests (from the repository root, with Ollama models qwen3:0.6b + bge-m3 when available)
   bash scripts/run_tests.sh   # pytest -v -n auto in each *_pkg/tests

   # Single package -- pytest MUST run from inside the package directory,
   # never from the repository root (each pyproject.toml sets asyncio_mode="auto"
   # and mcp_pkg adds -n 1).
   cd utils_pkg && pytest -v   # mcp tools are asyncio, single-process via addopts = -n 1
   cd mcp_pkg && pytest -v

   # Exclude tests that need an LLM
   cd utils_pkg && pytest -m "not localonly"

Notes: tests marked ``localonly`` require an LLM (some also need
docling/HF downloads).  CI runs them against ``ollama pull qwen3:0.6b
bge-m3`` and they self-skip when the backend is unreachable; use
``-m "not localonly"`` for a quick offline run.  MCP tests are
asyncio + single-process (``-n 1``).  ``utils_pkg/tests/test_stores_retrieval.py``
reads ``STORES_TEST_CONFIG`` (default ``stores-tests.json``).

Pre-commit
~~~~~~~~~~

.. code-block:: bash

   pre-commit run --all-files   # CI gate; ruff checks changed files on PRs via .github/workflows/ruff.yml

CLI verification
~~~~~~~~~~~~~~~~

If you modify a CLI entry point, confirm it starts (heavy imports must
stay deferred inside the Typer command so ``--help`` does not eager-import
the dependency chain -- see ``AGENTS.md`` CLI conventions)::

   <cli-name> --help

AI use
------

This repository includes an ``AGENTS.md`` file with instructions for AI
coding assistants.  While it is fine to use AI for development, it is
expected that the human reviews every line of code before submitting PRs.
PRs that are AI only and have not been checked by humans will not be
accepted.  All AI assistance must be clearly noted in the PR
description -- the PR template (``.github/PULL_REQUEST_TEMPLATE.md``)
requires you to confirm manual review of all AI-generated code and to
name the AI agent(s) used.

Pull requests
-------------

* Open PRs targeting the ``development`` branch.
* Keep changes focused -- one logical change per PR.
* Ensure CI passes (lint, typecheck, tests).
* Include a conventional commit message with issue numbers when applicable.
* Follow the PR template (``.github/PULL_REQUEST_TEMPLATE.md``); its
  checklist covers the branch target, tests, CI, commit message
  guidelines (https://chris.beams.io/git-commit) and conventional commits
  (https://www.conventionalcommits.org/en/v1.0.0/).

Developer docs
--------------

Architecture and component contracts (Mermaid diagrams, data-flow notes)
live in ``devdocs/system/`` and Architecture Decision Records in
``devdocs/adr/`` (MADR format) -- see ``devdocs/README.md``.  These are
internal notes for contributors and are intentionally separate from the
public ``docs/`` site.  See :doc:`developer-info` for an overview and links
to the developer docs on GitHub.
