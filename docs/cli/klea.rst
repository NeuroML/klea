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
