Terminal interface
==================

Klea's terminal interface is a full-screen `Textual <https://textual.textualize.io/>`_
application.  It talks to the same backend API as the :doc:`web-ui`, so the
sessions, models and graph state are shared.  It is started with
``klea-rag tui`` (RAG) or ``klea tui`` (agent).

The Textual dependency is an optional extra; install it with the ``tui``
extra (``pip install klea-utils[tui]`` or the ``full`` extra).  Without it,
``tui`` exits with a message pointing at the extra.  The process title is set
to the frontend name (for example ``klea-agent-tui``) so it is identifiable in
``ps``/``top`` instead of ``python``.

The layout is a horizontal split: a chat pane on the left and a full-height
status pane on the right.  The title and server line, and the active chat's
name and id, sit at the top of the status pane.

Chat pane
---------

The chat pane holds a transcript/inspector toggle, the transcript, the live
turn-status line, and the input.  The answer appears when the turn completes
(there is no live token typing).  The input soft-wraps and grows with the
text (up to a cap, then it scrolls); the cursor sits on the bottom line and
the box grows upward.  :kbd:`Enter` sends and :kbd:`Ctrl+J` inserts a newline
(:kbd:`Shift+Enter` also works on terminals that report the shifted key).

The transcript renders each message MIME-aware, mirroring the web: Markdown
for the agent's reply, syntax-highlighted diffs and code blocks for file
changes and tool output, and a plain-text fallback for anything that cannot
be rendered (there is no inline media in a terminal).  A long message is
collapsed to a few lines; click it, or focus it and press :kbd:`Enter`, to
expand or collapse.  Drag over text to select it; :kbd:`Ctrl+C` copies the
selection.  Text in the status/inspector panes, dialogs and prompts is
selectable too, and a drag in a message does not toggle it.

A failed run that can be recovered shows a **Retry** action; :kbd:`Ctrl+R`
resumes it from its checkpoint, re-running only the failed step.  If the
agent pauses for your input, the ask replaces the composer below the
transcript (see below), so the chat stays scrollable until you answer or
cancel.

.. figure:: /_static/images/klea-tui-chat.png
   :alt: Klea terminal interface chat view showing the transcript and the status panel
   :width: 80%
   :align: center

   The chat pane: the transcript, the live turn-status line and the status pane.

Status pane
-----------

The right pane summarises the current chat: its title and server line, its
**name and id**, and, in the agent, a read-only **mode** and tool **access**
line (change them with ``/mode`` or ``/access``; there are no in-panel
selectors) plus a read-only **project root**.  Below that are the chat's
selected **models** -- a collapsible group (collapsed by default) listing one
role per line with its provider, and a warning when a required model or API key
is still missing -- the running token totals (thousands-separated), and the
per-step state sections that stream while the graph runs.  Everything down to
the token totals stays fixed while only the state sections scroll.  The groups
are spaced apart, and the state sections sit on the pane's own background.

Inspection pane
---------------

Press :kbd:`Ctrl+I` (or :kbd:`Tab`, which is the byte a terminal sends for
it) to switch the same pane to the **inspection** view: the per-step trace
behind the latest answer (each pipeline step's heading, timing, summary and
raw structured details), grouped per query.

.. figure:: /_static/images/klea-tui-inspect.png
   :alt: Klea terminal interface inspector view showing the per-step pipeline trace
   :width: 80%
   :align: center

   The inspection pane: the per-step trace behind the latest answer.

Keys
----

.. list-table::
   :header-rows: 1

   * - Key
     - Action
   * - :kbd:`Enter`
     - Send the query or ``/command``
   * - :kbd:`Ctrl+J`
     - Insert a newline (the input grows, then scrolls, for a long entry)
   * - :kbd:`Shift+Enter`
     - Insert a newline too, on terminals that report the shifted key
   * - :kbd:`Ctrl+C`
     - Copy selected text
   * - :kbd:`Ctrl+Shift+C`
     - Stop the running query (:kbd:`F12` is the fallback where the terminal
       intercepts the shortcut)
   * - :kbd:`Ctrl+R`
     - Retry the last failed run (when recoverable)
   * - :kbd:`Ctrl+I`
     - Toggle the transcript / inspector view (:kbd:`Tab` is equivalent, as a terminal sends Ctrl+I as Tab)
   * - :kbd:`Ctrl+S`
     - Open the session picker
   * - :kbd:`Ctrl+M`
     - Open the model dialog
   * - :kbd:`Ctrl+Q`
     - Quit (asks to confirm first)

Copying uses the terminal's OSC 52 clipboard.  Terminals without OSC 52
support (for example macOS Terminal) may not update the system clipboard;
use the terminal's own selection modifier (often :kbd:`Shift`) as a fallback.

Sessions
--------

The TUI shows one active session at a time.  :kbd:`Ctrl+S` (or ``/sessions``)
opens a picker listing the server's chats; choosing one switches to it, and
the picker can also **rename** or **delete** a chat (deleting asks for
confirmation first).  The highlighted chat's first-query preview is shown
below the action buttons.  Switching is refused
while a run is streaming.  On launch, ``--continue`` opens the most recently
updated chat and ``--resume <chat_id>`` opens a specific one.

Commands
--------

Type ``/`` in the input for a command menu.  ``/help`` lists the available
commands: the TUI merges the server's command catalogue with the commands it
handles itself.  A client-side command (``/help``, ``/new``, ``/sessions``,
``/model``, ``/exit``) runs locally; a server-side command (such as ``/mode``
or ``/access``, the agent's operating mode and tool access level) is sent to
the agent.  An unknown command is reported locally and never sent.  ``/exit``
stops any active run and quits immediately (typing it is deliberate, so -- unlike
:kbd:`Ctrl+Q` -- it does not ask to confirm).  See
:doc:`commands` for the list.

Models and API keys
-------------------

:kbd:`Ctrl+M` (or ``/model``) opens the model dialog.  Each role's model is
chosen with a **provider** field, a **model** field, and an optional **custom
URL**, filled from the `models.dev <https://models.dev>`_ catalogue; free text
is accepted for models not in the catalogue.  **Manage API keys** opens the
provider-scoped credentials editor: keys are stored per provider on the
server, returned only in masked form, and expire after a period unused.

Human-in-the-loop
-----------------

When the agent needs input, the ask replaces the composer below the
transcript (the input box is hidden while it is active, so the chat stays
scrollable): a plan awaiting review shows the plan with **Approve** /
**Request changes** / **Cancel** controls, a plan blocked on a missing fact
shows one field per question, and a tool call that would touch a path outside
the project directory (or a sensitive file inside it, such as ``.env``) shows
one **Once** / **Session** / **Deny** choice per path.  Answering (or
requesting a revision) resumes the same run with its state intact.

Identity
--------

The user id resolves in one place: ``--user-id``, else ``KLEA_USER_ID``, else
a persisted TUI-local UUID.  The local UUID is sent to the server only when it
is a loopback address, so a local TUI does not accidentally share an identity
with a remote deployment.

.. note::

   The screenshots on this page are captured from a local deployment and
   may lag the latest interface.

.. seealso::

   * :doc:`web-ui` -- the browser interface over the same backend
   * :doc:`commands` -- the session-command catalogue
   * :doc:`../guides/create-and-use-rag` -- build a RAG system and
     query it through this interface
   * :doc:`rag` -- the pipeline the inspection pane exposes
