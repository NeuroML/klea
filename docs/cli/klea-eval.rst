klea-eval
=========

Run, score and report on the Klea evaluations (ADRs 0056-0060).

For a step-by-step walkthrough of a first run -- prerequisites, the JSONL
suite format, and how to read the stored results -- see
:doc:`../guides/run-an-evaluation`.

``klea-eval`` is the Klea-specific evaluation harness: it exists to evaluate
Klea and is not a general-purpose evaluation harness or benchmarking
platform.  Every suite, fixture and check targets Klea's own agent or RAG
server.

.. typer:: klea_eval.ui.cli:app
   :prog: klea-eval
   :show-nested:
   :width: 70
   :preferred: text

Environment variables
---------------------

``KLEA_EVAL_BASE_URL``
    Default base URL of the server for ``run``; there is no built-in default,
    and ``run`` exits ``2`` when neither the variable nor ``--base-url`` is
    set.

``KLEA_EVAL_WORKSPACE``
    Default workspace root for the run artefacts and for the inspection
    commands (``runs``, ``show``, ``summary``, ``prune``, ``check``) when
    ``--workspace`` is not given.  ``run`` falls back to the current
    directory when this is unset too.

``KLEA_LOG_LEVEL``
    Default for ``--log-level``; ``INFO`` when unset.

``OPENCODE_API_KEY`` (and the ``<PROVIDER>_API_KEY`` convention)
    Credential for the provider named by a ``--model`` override's prefix.  A
    model such as ``opencode:deepseek-v4.1-flash`` is served by the
    ``opencode`` provider and reads ``OPENCODE_API_KEY``, not
    ``OPENAI_API_KEY``.  ``OPENAI_API_KEY``, ``ANTHROPIC_API_KEY``,
    ``MISTRAL_API_KEY`` and ``HF_TOKEN`` cover their own providers, and a local
    ``ollama`` runtime needs none.

``run`` sets model roles with ``--model`` / ``-m ROLE=MODEL`` (repeatable).
The model string carries its provider prefix, for example
``opencode:deepseek-v4.1-flash``, and every role the run resolves -- ``chat``,
``plan`` and ``tool_picker`` -- should be overridden so none is left on a
default the target server cannot serve.  A run also needs ``--base-url`` (or
``KLEA_EVAL_BASE_URL``) before it starts, and ``--timeout`` raised above its
60 s default for suites whose turns run long agent paths.

Exit codes
----------

``run`` (and the in-process scheduler) return:

* ``0`` -- every case passed.
* ``1`` -- a case failed.
* ``2`` -- a configuration or validation fault, raised before any case ran;
  nothing is stored.
* ``4`` -- ``EXIT_GRADE_MISSING``: a completed case declared rubrics but
  recorded no grade.  A real case failure (``1``) takes precedence over a
  grade-missing case (``4``).

``check`` returns:

* ``0`` -- the index is consistent.
* ``1`` -- drift was found and not repaired.
* ``2`` -- a configuration or read fault.
* ``3`` -- drift a repair cannot fix, such as an unmarked directory or an
  unreadable ``run.json``.
