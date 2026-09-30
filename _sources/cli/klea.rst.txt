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
    Config file used when ``--profile`` is not given (from the env file,
    the environment, or the default ``klea_agent.json``).

Pass ``--profile <name>`` to load ``<name>.json`` from the current
directory or the config directory; ``--profile template`` scaffolds a
new config.  A profile only applies to a server the client spawns
itself -- a server that is already running keeps its own config.
