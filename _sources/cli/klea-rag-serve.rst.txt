klea-rag-serve
==============

Run the Klea RAG API server.

.. typer:: klea_rag.api.server:serve_app
   :prog: klea-rag-serve
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
new config.
