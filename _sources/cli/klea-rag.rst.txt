klea-rag
========

Interactive RAG query client.

.. typer:: klea_rag.ui.cli:rag_app
   :prog: klea-rag
   :show-nested:
   :width: 70
   :preferred: text

Environment variables
---------------------

``KLEA_RAG_ENV_FILE``
    Path to environment file (default: ``rag.env``).

``KLEA_RAG_APP_CONFIG_FILE``
    Config file used when ``--profile`` is not given (from the env file,
    the environment, or the default ``klea_rag.json``).

Pass ``--profile <name>`` to load ``<name>.json`` from the current
directory or the config directory; ``--profile template`` scaffolds a
new config.  A profile only applies to a server the client spawns
itself -- a server that is already running keeps its own config.
