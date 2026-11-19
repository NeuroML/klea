klea-eval
=========

Run, score and report on the Klea evaluations (ADRs 0056-0060).

For a step-by-step walkthrough of a first run -- prerequisites, the JSONL
suite format, and how to read the stored results -- see
:doc:`../guides/run-an-evaluation`.

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
