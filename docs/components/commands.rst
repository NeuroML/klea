Session commands
================

Klea's frontends accept session commands: type ``/`` at the start of the
input for a menu of the available commands, then press Enter.  A command is
invoked by the user (not chosen by the model) and acts on the session itself
-- for example changing the agent's operating mode -- instead of being a
normal query.  Some commands use a model internally; the distinction is who
invokes them, not whether they use an LLM.

The frontend fetches the backend's server-side command catalogue
(``GET /commands``) and merges it with the commands it handles itself.  A
**client** command runs in the frontend; a **server** command is sent to the
graph, where a command node runs it.  An unknown command is reported in the
frontend and never sent, and a command's output is shown as a system block in
the chat.

Only the implemented commands are listed here (and offered in the ``/``
menu); more are planned in both groups.

.. note::

   Client commands are shared by the web UI and the TUI.  The lightweight
   ``klea cli`` REPL has no client-command handling, so only server commands
   work there.  The TUI offers ``/help``, ``/new``, ``/sessions``, ``/model``
   and ``/exit``; the web UI also offers ``/rename``, ``/export`` and the
   ``/sessions`` verbs.  Server commands depend on the app's graph: the
   agent registers ``/mode``, ``/access``, ``/cwd``, ``/init`` and
   ``/compact``, while the RAG graphs register none yet.

UI commands (client)
--------------------

These run in the frontend.

.. list-table::
   :header-rows: 1

   * - Command
     - Usage
     - Where
     - Description
   * - ``/help``
     - ``/help [command]``
     - web, TUI
     - List the available commands, or show one command's usage.
   * - ``/new``
     - ``/new``
     - web, TUI
     - Start a new session.
   * - ``/rename``
     - ``/rename <name>``
     - web
     - Rename the current session.
   * - ``/model``
     - ``/model``
     - web, TUI
     - Open the model and API-key configuration.
   * - ``/export``
     - ``/export``
     - web
     - Download the session transcript as Markdown.
   * - ``/sessions``
     - ``/sessions`` (TUI picker); ``/sessions [list]`` /
       ``/sessions switch <n|id>`` / ``/sessions delete <n|id>`` (web)
     - web, TUI
     - List the sessions (pinned first), switch the active session by number
       or id, or delete a session (with confirmation).  The TUI opens a picker
       that does all of this.
   * - ``/exit``
     - ``/exit``
     - web, TUI
     - Stop the frontend.  In the web UI this stops the server it launched and
       closes any active runs; in the TUI it stops any active run and quits.
       The web header's exit button and the TUI's :kbd:`Ctrl+Q` ask to confirm
       first; the ``/exit`` command does not.

A command may define **subcommands** (verbs); ``/help <command>`` lists them,
and the ``/`` menu autocompletes them.

Graph commands (server)
-----------------------

These are sent to the graph and run there.

.. list-table::
   :header-rows: 1

   * - Command
     - Usage
     - Description
   * - ``/mode``
     - ``/mode [general|scientific]``
     - Show or set the agent's operating mode (see :doc:`agent`); with no
       argument it reports the current mode.
   * - ``/access``
     - ``/access [read_only|full]``
     - Show or set the tool access level; with no argument it reports the
       current level.
   * - ``/cwd``
     - ``/cwd [path]``
     - Show or set the session project root -- the directory the file tools
       operate in and the default permission boundary.  The path must be an
       existing directory; with no argument it reports the current root.
   * - ``/compact``
     - ``/compact``
     - Summarise and shorten the conversation memory now (Agent, when
       conversation memory is enabled).  The outcome is shown in the inspection
       pane.
   * - ``/init``
     - ``/init``
     - Generate project guidance (``AGENTS.md``) for the current project so
       future sessions pick up its conventions (Agent, full access).

``/init`` writes ``AGENTS.md`` in the project directory, so its file-write
needs the ``full`` tool access level; ``/compact`` is offered only when
conversation memory is enabled (see :doc:`agent`).

Disabling commands
------------------

A deployment can disable commands with ``general.commands.disabled`` (a list
of command names) in the agent config; a disabled command is neither offered
nor runnable.

.. seealso::

   * :doc:`web-ui` -- the web interface, including the command menu
   * :doc:`tui` -- the terminal interface, including the command menu
   * :doc:`agent` -- the agent and its operating modes
   * :doc:`../install` -- configuration
