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

   Session commands are available in the agent (``klea web`` / ``klea cli``).
   The RAG frontends do not expose any yet.

UI commands (client)
--------------------

These run in the frontend.

.. list-table::
   :header-rows: 1

   * - Command
     - Usage
     - Description
   * - ``/help``
     - ``/help [command]``
     - List the available commands, or show one command's usage.

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

Disabling commands
------------------

A deployment can disable commands with ``general.commands.disabled`` (a list
of command names) in the agent config; a disabled command is neither offered
nor runnable.

.. seealso::

   * :doc:`web-ui` -- the web interface, including the command menu
   * :doc:`agent` -- the agent and its operating modes
   * :doc:`../install` -- configuration
