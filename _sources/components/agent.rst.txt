Klea Agent
==========

The Klea agent is Klea's general-purpose agentic assistant, built on the
shared :class:`~klea_utils.graph.base.BaseLangGraph` orchestrator (see
:doc:`utils`).  It plans and carries out research tasks -- literature
review, hypothesis generation, planning, coding, pipeline execution and
analysis -- using the tools supplied by its configured MCP servers (see
:doc:`mcp-servers`).

.. note::

   ``klea_agent`` is under active development and is not released yet.
   Some paths described here are specified but not fully wired; this page
   says so explicitly wherever that is the case.

Operating modes
---------------

The agent runs in one of two modes, decided at task entry (ADR-0030):

General
   The default.  Answers and tasks are handled with the configured models
   and tools.

Scientific
   Intended for grounded, source-backed research answers over a curated
   knowledge source.  **Not yet runnable:** the agent has no curated
   knowledge source wired in (retrieval is deferred to the ADR-0029
   phase), so a Scientific request is declined with an explanation rather
   than silently answered without grounding.

A requested mode that cannot run is reported back to the user, and the
effective mode is surfaced to the web UI through the graph's ``context``
event (see :doc:`web-ui`).

How a task runs
---------------

The task path is a `LangGraph <https://langchain-ai.github.io/langgraph/>`_
state machine.  In outline:

1. **Route** -- a narrow entry router classifies each request as ``chat``
   (answered inline) or ``task`` (handed to the planner).  Trivial chat
   takes this short path; everything else is planned.
2. **Plan** -- the Planner writes the goal and an ordered plan, and never
   answers the user directly.
3. **Review and clarification** -- the run can pause for human input and
   resume the same run with its state intact (LangGraph interrupt/resume).
   A plan can carry a review step for an explicit approve/revision decision,
   either before any work runs or at a checkpoint after an earlier step (for
   example "review after step 3"); a revision returns the feedback to the
   Planner.  A plan that needs a missing fact asks the questions its blocked
   step carries.
4. **Work loop** -- the plan is a dependency graph: independent steps run
   together in a batch, while a step that needs an earlier step's result waits
   for it.  The tools picker binds the arguments for the batch, the tool caller
   dispatches them concurrently (except calls that touch the same file, which
   run in order), and a deterministic triage router plus an operational
   evaluator decide, per step, whether to retry the step, replan, or move on.
   If the picker finds no suitable tool, the plan is revised rather than
   looping.  Before dispatch, a call that would touch a path outside the
   project directory -- or a sensitive file inside it, such as ``.env`` --
   pauses for a per-path allow-once / allow-for-session / deny decision; a
   denied path returns a non-halting error and the run continues (see
   :doc:`mcp-servers`).
5. **Answer** -- once the plan is done, the answer is composed from the
   step results and delivered.

Memory
------

Conversation history is summarised per session, so long-running chats stay
within the model's context window: the most recent history is kept verbatim
and older history is folded into a running summary that the node prompts
reuse.  The ``general.memory`` config tunes this per deployment -- the
``summarisation_threshold_chars`` (how much old history accumulates before
summarising; default 40000) and ``num_history_chars`` (how much recent
history stays verbatim; default 20000).

``/compact`` triggers a summarisation on demand (for example to free context
before a large task).  The outcome -- how much was summarised, or that there
was nothing to summarise -- is shown in the inspect pane, not the chat, since
the summary is internal memory rather than a reply.

Session commands
----------------

A run is steered with in-session ``/commands``: type ``/`` in the input for
the menu.  ``/mode`` sets the operating mode, ``/compact`` summarises the
memory, ``/init`` generates project guidance, and ``/help`` lists the
available commands.  A client command runs in the frontend; a graph command
runs in the graph and may itself use the model (for example ``/init``).  See
:doc:`commands`.

Project context
---------------

The agent reads a project instruction file from its working directory and
loads it into session-scoped *discovery*: ``AGENTS.md`` is preferred, with
``CLAUDE.md`` as the fallback (the first one present wins).  The file is
read once per session and re-read only when its modification time changes,
so editing it updates the agent without restarting the server.

The discovered text is rendered into the **Planner** and the final
**answer-composer** prompts, so project conventions (build and test
commands, code layout, house style) inform both how a task is planned and
how the reply is written.  It is not injected into the per-round nodes,
which see project specifics through the plan instead.  A project with no
instruction file simply has no extra context; run ``/init`` to generate one.

Tool access
-----------

The agent defaults to the ``full`` tool access level, and the level can be
changed per chat in the web UI's status pane.  Under ``read_only`` only
tools annotated read-only are offered, so file listing, reading and search
(``read_file`` / ``grep`` / ``find_files``) stay available while destructive
tools such as ``run_command`` and the file editors (``write_file`` /
``edit_file``) do not.  See :doc:`mcp-servers` for the access model and its
sandboxing caveats; environment and coding requests are answerable in full
mode through the bundled ``run_command``, ``write_file`` and ``edit_file``
tools.

Status
------

The agent is unreleased.  Notably still in progress:

* the Scientific mode knowledge source (retrieval, ADR-0029);
* grounding/assurance enforcement -- answers are labelled ``unverified``
  until this lands.

.. seealso::

   * :doc:`mcp-servers` -- the tools the agent can call
   * :doc:`tools` -- the bundled tools it can call
   * :doc:`web-ui` -- the interface, including the mode and access selectors
   * :doc:`../cli/klea` -- the agent CLI reference
