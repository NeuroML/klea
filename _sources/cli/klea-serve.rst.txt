klea-serve
==========

**WIP: coming soon** -- this CLI is under active development and not yet
ready for general use.

Klea agent API server.

.. typer:: klea_agent.api.server:serve_app
   :prog: klea-serve
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
new config.
