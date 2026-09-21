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
3. **Review** -- the plan can pause for human review.  The review step
   currently auto-approves (a canned approval); real interactivity
   (LangGraph interrupt/resume) is pending.
4. **Work loop** -- the plan is a dependency graph: independent steps run
   together in a batch, while a step that needs an earlier step's result waits
   for it.  The tools picker binds the arguments for the batch, the tool caller
   dispatches them concurrently (except calls that touch the same file, which
   run in order), and a deterministic triage router plus an operational
   evaluator decide, per step, whether to retry the step, replan, or move on.
   If the picker finds no suitable tool, the plan is revised rather than
   looping.
5. **Answer** -- once the plan is done, the answer is composed from the
   step results and delivered.

Memory
------

Conversation history is summarised per session, so long-running chats stay
within the model's context window.

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
  until this lands;
* interactive human plan review (currently auto-approving).

.. seealso::

   * :doc:`mcp-servers` -- the tools the agent can call
   * :doc:`web-ui` -- the interface, including the mode and access selectors
   * :doc:`../cli/klea` -- the agent CLI reference
