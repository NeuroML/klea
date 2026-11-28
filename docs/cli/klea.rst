klea
====

**WIP: coming soon** -- this CLI is under active development and not yet
ready for general use.

General-purpose research agent client.

.. typer:: klea_agent.ui.cli:agent_app
   :prog: klea
   :show-nested:
   :width: 70
   :preferred: text

Environment variables
---------------------

``KLEA_AGENT_ENV_FILE``
    Path to environment file (default: ``klea_agent.env``).

``KLEA_AGENT_APP_CONFIG_FILE``
    Config file used when ``--profile`` is not given (from the env file or
    the environment).  There is no default config file: without this and
    without ``--profile`` the agent runs from built-in defaults.

Pass ``--profile <name>`` to load ``<name>.json`` from the current
directory or the config directory; ``--profile template`` scaffolds a
new config.  A profile only applies to a server the client spawns
itself -- a server that is already running keeps its own config.

Listing sessions
----------------

``klea list-sessions`` prints the chat sessions the ``tui``, ``web`` and
``headless`` clients share, newest first.  Each session is one block: a header
line with the chat id, when the chat was last used and its title, then the
preview of its first query quoted beneath it::

    * olivine-caracal | 2026-01-12 14:35 | Olivine Caracal
    > How do I run the tests?
    >

    * basalt-lynx | 2025-12-02 09:08 | Basalt Lynx
    > Summarise the paper.
    >

The ``> `` lines carry the server's preview text verbatim and the lone ``>``
closes the quote; a chat with no preview yet prints the header line only.
Blocks are separated by a single blank line, and a header omits the title
field (and its ``|``) when the chat has no title.  It is a client in the same
way as ``headless``, honouring ``-s/--server`` and spawning a local server
when none is running.

``-n/--limit`` caps how many are printed (default 20; ``0`` prints all),
``--no-preview`` drops the quoted preview block (header line only) and
``--json`` prints one array of
``chat_id`` / ``title`` / ``updated_at`` / ``preview`` objects for scripting.
The printed chat id is what ``--resume`` takes, so a session can be found in
the listing and opened with ``klea tui --resume <chat_id>`` or ``klea web
--resume <chat_id>``.

Headless runs
-------------

``klea headless "<query>"`` runs one query non-interactively, like ``tui``
and ``web`` it is a client of the backend and takes the same ``-s/--server``
option (default ``http://127.0.0.1:8006``):

* when nothing is listening on a loopback URL, a server is spawned for the
  run and stopped again when it exits;
* a server that is already listening, or one on a remote host, is used as-is
  and left running.

``--profile`` therefore only applies to a server ``headless`` spawns itself;
an already-running server keeps its own config.  ``--thread-id`` names the chat
the run is checkpointed under (default ``default_thread``) and ``--user-id``
resolves like the other clients (``--user-id``, else ``KLEA_USER_ID``, else a
persisted local UUID for a loopback server), so a headless run appears in the
same session list as ``tui`` and ``web``.  ``--debug`` turns on debug logging
and is inherited by a spawned server.

The safety flags configure a server this command spawns; an already-running
server keeps its own settings:

``--path-check strict|declared|off``
    Which tool-call paths are gated (default ``strict``).  With ``off`` no call
    can ever pause for a path approval, which is what an unattended run needs.

``--path-check-stance deny|allow``
    How an unapproved detected path is treated; only read when HITL is off.
    ``deny`` fails the call, ``allow`` lets it proceed without a prompt.

``--hitl/--no-hitl``
    Human-in-the-loop approvals (default on).  With HITL on the run stops at
    the first ask, prints it and exits 1 with the thread left paused, so it can
    be answered from another client (for example ``klea tui``); use
    ``--no-hitl`` for unattended runs.

``--unattended``
    Shorthand for a run that never prompts: sets ``--path-check off``,
    ``--path-check-stance allow`` and ``--no-hitl``.  It is a convenience for
    external or third-party evaluation harnesses that drive ``klea headless``
    and cannot answer a prompt.  Like the other safety flags it only
    configures a server ``klea headless`` spawns itself; an already-running or
    remote server keeps its own settings, so it does not by itself guarantee a
    prompt-free run there.  A contradicting individual flag (for example
    ``--hitl`` or ``--path-check strict``) is rejected.
