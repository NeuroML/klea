Running an evaluation
=====================

``klea-eval`` runs a suite of test cases against a live Klea server, scores
each case, and stores the result so it can be inspected, summarised and
reported on afterwards.  This page takes you from a running server to a
stored, readable run.  See :doc:`../cli/klea-eval` for the complete command
reference.

``klea-eval`` is the Klea-specific evaluation harness: it exists to evaluate
Klea and is not a general-purpose evaluation harness or benchmarking
platform.  Every suite, fixture and check targets Klea's own agent or RAG
server.

Prerequisites
-------------

* An environment with the package installed from the repository, so the
  ``klea-eval`` console script is on ``PATH``::

      uv pip install -e eval_pkg

* A running Klea server.  The harness drives the same ``/query/stream`` SSE
  route the web and TUI clients use, so either server is a valid target:

  - ``klea-serve``, the agent, at ``127.0.0.1:8006``;
  - ``klea-rag-serve``, the RAG pipeline, at ``127.0.0.1:8005``.

* The base URL of that server.  Pass ``--base-url`` / ``-u`` or export
  ``KLEA_EVAL_BASE_URL``.  There is no default: ``run`` exits ``2`` with
  ``error: no base URL configured; ...`` when neither is supplied.  The
  endpoint path defaults to ``/query/stream``; pass ``--endpoint`` only for a
  different surface.

* Model overrides for a run, passed with ``--model`` / ``-m ROLE=MODEL``.  A
  model name carries its provider prefix, for example
  ``opencode:deepseek-v4.1-flash``.  Override every role the run resolves
  (``chat``, ``plan`` and ``tool_picker``) so no role is left on a default the
  target server cannot serve; a role a case's own ``run_config`` pins still
  wins for that role.

* Credentials for the provider of any ``--model`` override you use, taken
  from the environment of the harness process using the ``<PROVIDER>_API_KEY``
  convention: the ``opencode:`` prefix needs ``OPENCODE_API_KEY`` (not
  ``OPENAI_API_KEY``), while ``OPENAI_API_KEY``, ``ANTHROPIC_API_KEY``,
  ``MISTRAL_API_KEY`` and ``HF_TOKEN`` cover their own providers and a local
  ``ollama`` runtime needs none.  A key is pushed only to the run's own user
  scope and is never printed, echoed in an error, or recorded in the run's
  artefacts.  A locked role rejects an override with ``403`` and
  ``The '<role>' model is locked``; the lock is a server-side setting.

* ``--timeout`` above its 60 s default for suites whose turns run long agent
  paths, and a ``--workspace`` / ``-w`` directory outside the repository so
  the run artefacts do not pollute the working tree.  The agent works in the
  run's own ``workspace/`` directory, not the server's working directory, and
  a case's execution-check ``command`` runs there too.

Writing a suite
---------------

A suite is a JSONL file: one JSON object per line, one case per line.  The
loader relies on these fields:

``case_id``
    Required.  Identifies the case in the run index, in the stored artefacts
    and in the per-case line the CLI prints.

``schema_version``
    Required.  A string pinning the case layout; the only accepted value is
    ``"1.0"``.  Any other value, or a non-string, is a validation error.

``turns``
    Required.  The ordered turns one case sends.  Each turn carries
    ``turn_number`` (1-based and matching its position in the list), ``role``,
    and the message text in ``content`` (required, a non-empty string).  The
    allowed turn keys are ``turn_number``, ``role``, ``content``,
    ``expected_outputs`` and ``tool_expectations``; any other key is a
    validation error.  The accepted roles are ``user``, ``plan``,
    ``execute``, ``eval`` and ``chat``.

``rubrics``
    Optional.  Per-case grading criteria layered on top of task success
    (ADR-0056, ADR-0061).

Optional per-case ``fixtures`` are resolved relative to the directory of the
JSONL file that owns the case, so a suite can carry its own fixture tree.  A
minimal case looks like this:

.. code-block:: json

   {"case_id": "smoke-001", "schema_version": "1.0", "turns": [{"turn_number": 1, "role": "user", "content": "Summarise the NeuroML repository layout."}]}

Validate before running
-----------------------

``validate`` loads the suite without touching the network, so it is the
cheapest way to catch a bad ``turn_number``, an unknown ``schema_version`` or
a misplaced key::

    klea-eval validate suites/smoke.jsonl

It prints ``<source>: <n> cases valid``, or the first fault as
``<source>:<line>: <message>``, and exits ``2``.

Running a suite
---------------

::

    klea-eval run suites/smoke.jsonl \
        --base-url http://127.0.0.1:8006 \
        --workspace runs/smoke \
        --harness-seed 0 \
        --max-attempts 3

The options are:

``SUITE``
    The JSONL suite to run (positional, required).

``--workspace``, ``-w``
    Directory to write run artefacts into.  Defaults to
    ``$KLEA_EVAL_WORKSPACE``, else the current directory.

``--base-url``, ``-u``
    Base URL of the server.  Defaults to ``$KLEA_EVAL_BASE_URL``; required.

``--endpoint``
    Path appended to the base URL.  Defaults to ``/query/stream``.

``--timeout``
    Per-request timeout in seconds.  Defaults to ``60.0``; raise it for suites
    whose turns run long agent paths.

``--harness-seed``, ``-s``
    Seed for the deterministic harness.  Defaults to ``0``.

``--max-attempts``
    Attempts per case before it is recorded as failed.  Defaults to ``3``.

``--overwrite``
    Replace an existing run's ``run_id`` instead of skipping it.  Off by
    default.

``--model``, ``-m ROLE=MODEL``
    Override a model role for this run; repeatable.  A malformed pair is a
    configuration fault and exits ``2``.  The roles are whatever the target
    app exposes in its ``models/active`` map; an unknown role aborts the whole
    run before the first case with exit ``2`` and
    ``unknown role '<role>' (the backend exposes: ...)``.  A case's own
    ``run_config.model_overrides`` still wins over the flag for the same role.

Every command also accepts ``--debug`` (verbose logging) and ``--log-level``
(defaults to ``$KLEA_LOG_LEVEL``, otherwise ``INFO``).

Each case prints one line, ``<case_id>: pass`` or ``<case_id>: fail``, and the
command exits with the suite's exit code (see below).

A ``run_id`` is derived from the case's identity and the harness seed, not
from the wall clock, so rerunning the same suite with the same seed targets
the same stored record.  Without ``--overwrite`` that is not an error: the
case is logged as ``already complete, skipping it``, recorded as ``skipped``
with its stored metrics, and its directory is left untouched.  With
``--overwrite`` the existing run directory is cleared and the case is
republished under that ``run_id``.  To keep both runs, change
``--harness-seed`` or use a fresh ``--workspace``.

Reading the results
-------------------

::

    klea-eval runs                       # the run index, one line per run
    klea-eval runs --case-id smoke-001 --status completed
    klea-eval show <run_id>              # <run_id>: <case_id> <status> pass|fail
    klea-eval summary --k 1              # suite_score, pass_at_k, input_output_ratio
    klea-eval report results.json -o report.md

``runs`` prints ``<run_id> <case_id> <status> <timestamp>`` per stored run and
accepts ``--case-id`` and ``--status`` filters.  ``show`` reads one run back
through the API.  ``summary`` prints ``suite_score``, ``pass_at_k`` and the output-to-input
``input_output_ratio``
for ``--k`` attempts per case (default ``1``), plus the usage and retry lines
described in `Reading the run metrics`_ below.  ``report`` renders a report from
already collected results to ``--output`` / ``-o``, where ``-`` means standard
output.

Artefacts are written under ``--workspace``.  Each case runs in its own
directory, removed on success and on failure; what survives a successful run
is the stored result record, which is what the inspection commands read.  A
failed run leaves no stored record and appends no index line.

``show``, ``runs``, ``summary``, ``prune`` and ``check`` accept ``--workspace``
/ ``-w`` and fall back to ``$KLEA_EVAL_WORKSPACE``.

Reading the run metrics
-----------------------

Each stored turn, run and suite aggregate also carries the usage and retry
metrics the stream reports.  ``klea-eval summary`` prints the suite-level ones
after ``input_output_ratio``, and a fetch of a single run exposes the same
fields per turn and per case::

    llm_calls: 7
    peak_context_tokens: 41231
    evaluator_retry_total: 2
    first_try_pass_rate: 0.6667

``llm_calls``
    The LLM calls a turn made, summed over a run's turns and over a suite's
    cases.

``per_call_usage``
    The per-invocation usage entries of those calls: kept per turn, and
    concatenated in order on a run.  A record that carries none yields an
    empty list rather than raising, so an older stored run still aggregates.

``peak_context_tokens``
    The largest input-plus-output token count of a single call, taken over a
    run's turns and then over a suite's cases.  A turn without per-call usage
    reports no peak, which the summary prints as
    ``peak_context_tokens: n/a``.  The peak spans every call a turn made,
    retried calls included, so it is a retry-merged upper bound and not the
    context size of one attempt: do not read it as the size of a single
    prompt.

``evaluator_retry_count``
    The evaluator retries retained on one turn; ``evaluator_retry_total`` is
    their sum over a suite's cases.

``max_attempt_seen``
    The highest ``attempt`` counter among a turn's retained retries, and ``0``
    when the stream sent none.

``first_try``
    ``true`` when the turn's work was accepted on its first attempt, ``false``
    when an evaluator retry was recorded, and ``null`` when the stream carried
    neither a retry nor a verdict.

``first_try_passes`` and ``recovered_after_retry``
    Cases whose first attempt was accepted, and cases that only passed after
    a retry.

``first_try_pass_rate``
    The mean of the per-case first-try pass rates.  It is ``null`` -- printed
    as ``first_try_pass_rate: n/a`` -- when no case carried an evaluator
    verdict, so a missing denominator stays explicit instead of reading as
    ``0.0``, and a turn without a verdict is left out of the rate rather than
    counted as a clean pass.

The retry detail is reported on the first retried step of a batch, so a batch
that retried several steps is counted once: ``evaluator_retry_total`` is a
lower bound on the retries actually performed, not an exact tally.

Housekeeping
------------

::

    klea-eval check                      # index vs run directories
    klea-eval check --repair --json
    klea-eval prune --keep 20            # archive beyond the window
    klea-eval prune --keep 20 --purge    # delete instead of archiving
    klea-eval prune --keep 20 --dry-run  # report the plan, touch nothing

``check`` compares the run index with the run directories and reports missing,
orphan, duplicate, unmarked and staging entries.  ``--repair`` fixes the drift
it can (re-indexing a complete run, dropping duplicate or orphan lines);
``--json`` emits the findings as one JSON document.

``prune`` applies the retention policy, keeping ``--keep`` / ``-k`` completed
runs per case (default ``20``).  Archiving is the default: a run beyond the
window loses only its workspace tree, while its ``run.json``,
``metrics.jsonl`` and index line survive and an archived marker is written
beside them, so the run stays listed and readable.  Pass ``--purge`` to delete
the whole run directory and drop its index line, or ``--dry-run`` to report
what would happen without touching the disk.  Retention only ever runs from
this explicit command.

Exit codes
----------

``run`` (and the scheduler) return:

* ``0`` -- every case passed.
* ``1`` -- a case failed.
* ``2`` -- a configuration or validation fault, raised before any case ran.
* ``4`` -- a completed case declared rubrics but recorded no grade
  (``EXIT_GRADE_MISSING``), which is distinct from the ``1`` of a real case
  failure.  A real case failure outranks a grade-missing case, so a run with
  both exits ``1``.

``check`` returns a different set:

* ``0`` -- the index is consistent.
* ``1`` -- drift was found and not repaired.
* ``2`` -- a configuration or read fault.
* ``3`` -- drift a repair cannot fix, such as an unmarked directory or an
  unreadable ``run.json``.

Troubleshooting
---------------

* **``error: no base URL configured; ...`` (exit 2).**  Neither ``--base-url``
  nor ``$KLEA_EVAL_BASE_URL`` was set.
* **Connection refused / ``endpoint request failed``.**  No server is
  listening on the base URL; start ``klea-serve`` (8006) or
  ``klea-rag-serve`` (8005) and check the scheme, host and port.
* **``endpoint answered 404``.**  The path appended to the base URL does not
  exist on that server.  Both Klea servers serve ``/query/stream``; drop
  ``--endpoint`` to get the default.
* **``stream ended without a complete event`` / ``endpoint reported an
  error``.**  The server answered but the turn failed inside the graph, or the
  target is not the Klea SSE contract.  Check the server log.
* **``endpoint timed out after 60.0s``.**  Raise ``--timeout``; long agent
  paths exceed the default.
* **A validation fault from ``validate`` / ``run`` (exit 2).**  The message is
  ``<source>:<line>: <message>``; common causes are a ``schema_version`` other
  than ``"1.0"``, a ``turn_number`` that does not match the turn's position,
  or an unknown turn key.
* **``unknown role '<role>' (the backend exposes: ...)``.**  A ``--model`` role
  the target app does not expose; the whole run aborted before the first case
  and nothing was stored.
* **A rerun reports ``skipped``.**  The deterministic ``run_id`` matched a
  complete stored run; pass ``--overwrite``, or change ``--harness-seed`` /
  ``--workspace`` to keep both runs.
