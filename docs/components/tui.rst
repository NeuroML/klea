Terminal interface
==================

Klea's terminal interface is a full-screen `Textual <https://textual.textualize.io/>`_
application.  It talks to the same backend API as the :doc:`web-ui`, so the
sessions, models and graph state are shared.  It is started with
``klea-rag tui`` (RAG) or ``klea tui`` (agent).

The Textual dependency is an optional extra; install it with the ``tui``
extra (``pip install klea-utils[tui]`` or the ``full`` extra).  Without it,
``tui`` exits with a message pointing at the extra.

The layout is a horizontal split: a chat panel on the left and a full-height
status panel on the right.

Chat panel
----------

The left panel holds the title and server line, a transcript/inspector
toggle, the transcript, the live turn-status line, and the input.  Answers
stream in as they are generated.

The transcript renders each message MIME-aware, mirroring the web: Markdown
for the agent's replies, syntax-highlighted diffs and code blocks for file
changes and tool output, and a plain-text fallback for anything that cannot
be rendered (there is no inline media in a terminal).  Press :kbd:`Ctrl+I`
to switch the same panel to the **inspector** view: the per-step trace behind
the latest answer (each pipeline step's heading, timing, summary and raw
structured details), grouped per query.

A failed run that can be recovered shows a **Retry** hint; :kbd:`Ctrl+R`
resumes it from its checkpoint, re-running only the failed step.  If the
agent pauses for your input, a review / input / permission dialog opens (see
below) and the input is held until you answer or cancel.

Keys
----

.. list-table::
   :header-rows: 1

   * - Key
     - Action
   * - :kbd:`Enter`
     - Send the query or ``/command``
   * - :kbd:`Ctrl+C`
     - Stop the running query
   * - :kbd:`Ctrl+R`
     - Retry the last failed run (when recoverable)
   * - :kbd:`Ctrl+I`
     - Toggle the transcript / inspector view
   * - :kbd:`Ctrl+S`
     - Open the session picker
   * - :kbd:`Ctrl+M`
     - Open the model dialog
   * - :kbd:`Ctrl+Q`
     - Quit

Sessions
--------

The TUI shows one active session at a time.  :kbd:`Ctrl+S` (or ``/sessions``)
opens a picker listing the server's chats; choosing one switches to it, and
the picker can also **rename** or **delete** a chat.  Switching is refused
while a run is streaming.  On launch, ``--continue`` opens the most recently
updated chat and ``--resume <chat_id>`` opens a specific one.

Commands
--------

Type ``/`` in the input for a completion list.  ``/help`` lists the available
commands: the TUI merges the server's command catalogue with the commands it
handles itself.  A client-side command (``/help``, ``/sessions``, ``/model``,
``/exit``) runs locally; a server-side command (such as ``/mode`` or
``/access``, the agent's operating mode and tool access level) is sent to the
agent.  An unknown command is reported locally and never sent.  ``/exit``
stops any active run and quits (the same as :kbd:`Ctrl+Q`).  See
:doc:`commands` for the list.

Models and API keys
-------------------

:kbd:`Ctrl+M` (or ``/model``) opens the model dialog.  Each role's model is
chosen with a **provider** field, a **model** field, and an optional **custom
URL**, filled from the `models.dev <https://models.dev>`_ catalogue; free text
is accepted for models not in the catalogue (for example ``ollama:`` models or
a self-hosted endpoint).  For a ``huggingface`` role the dialog also offers a
**Run** choice (hosted inference providers, or local) and, for the hosted
backend, an **inference provider** field.  Saving updates the current chat's
overrides (and its session defaults); **Reset** clears a role's override and
**Use for all roles** copies a model onto the other roles of the same kind.

**Manage API keys** opens the provider-scoped credentials editor: keys are
stored per provider on the server, returned only in masked form, and expire
after a period unused.  A query is blocked with a warning until the models a
run needs (and any required API keys) are configured; client commands such as
``/help`` still work.

Human-in-the-loop
-----------------

When the agent needs input, a modal opens over the screen:

* a plan awaiting review shows the plan with **Approve** / **Request changes**
  / **Cancel**;
* a plan blocked on a missing fact shows one field per question;
* a tool call that would touch a path outside the project directory (or a
  sensitive file inside it) shows one **Once** / **Session** / **Deny** choice
  per path.

Answering or requesting a revision resumes the same run with its state
intact; a pending ask is re-shown if the chat is reloaded.

Status panel
------------

The right panel summarises the current chat: in the agent, the operating
**mode** and tool **access** selectors (choosing one sends ``/mode`` or
``/access`` to the agent), the running token totals, and the per-step state
sections that stream while the graph runs.

Identity
--------

The user id resolves in one place: ``--user-id``, else ``KLEA_USER_ID``, else
a persisted TUI-local UUID.  The local UUID is sent to the server only when it
is a loopback address, so a local TUI does not accidentally share an identity
with a remote deployment.

Limitations
-----------

* No inline media: images and other rich MIME types degrade to a text
  fallback.
* No live token typing (answers appear on completion of the turn).
* No always-visible session sidebar; use the picker (:kbd:`Ctrl+S`).

.. seealso::

   * :doc:`web-ui` -- the browser interface over the same backend
   * :doc:`commands` -- the session-command catalogue
   * :doc:`../guides/create-and-use-rag` -- build a RAG system and query it
