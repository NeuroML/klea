Installation
============

Requirements
------------

* Python 3.12 or later
* A `LangChain-compatible inference provider
  <https://docs.langchain.com/oss/python/integrations/providers/overview>`_
  for LLM access (e.g. OpenAI, Anthropic, Ollama, HuggingFace, etc.)

klea-rag and klea-utils (PyPI)
-------------------------------

The RAG and utilities packages are available on PyPI::

   pip install klea-rag

This installs ``klea_rag`` and its core dependency ``klea_utils``.
Optional extras for vector store backends and document ingestion are
listed below — add them with e.g. ``pip install klea-rag[chroma]``.

If you use `uv <https://github.com/astral-sh/uv>`_, replace ``pip`` with
``uv pip``.

klea-utils extras
~~~~~~~~~~~~~~~~~

.. list-table::
   :header-rows: 1

   * - Extra
     - Installs
     - Purpose
   * - ``chroma``
     - ``langchain-chroma``, ``chromadb``
     - `Chroma <https://github.com/chroma-core/chroma>`_ vector store support
   * - ``pgvector``
     - ``langchain-postgres``
     - `pgvector <https://github.com/pgvector/pgvector-python>`_ support
   * - ``qdrant``
     - ``langchain-qdrant``
     - `Qdrant <https://github.com/qdrant/qdrant>`_ vector store support
   * - ``huggingface``
     - ``langchain-huggingface``
     - HuggingFace inference provider
   * - ``ollama``
     - ``langchain-ollama``, ``ollama``
     - Ollama inference provider (local models)
   * - ``anthropic``
     - ``langchain-anthropic``
     - Anthropic provider (native ``anthropic:`` and custom ``/messages`` endpoints)
   * - ``ingest``
     - ``docling``, ``typer``, ``xxhash``
     - Document ingestion pipeline
   * - ``nicegui``
     - ``nicegui``
     - NiceGUI web UI frontend
   * - ``search``
     - ``astral-dev-toolchain-ripgrep``
     - Pinned ripgrep binary for the read-only ``grep`` / ``find_files`` tools (falls back to an in-house walker without it)
   * - ``full``
     - All of the above except ``anthropic``
     - All optional extras (vector stores + inference providers + frontends); Anthropic stays opt-in

Usage::

   pip install klea_utils[chroma]

klea-rag extras
~~~~~~~~~~~~~~~

.. list-table::
   :header-rows: 1

   * - Extra
     - Installs
     - Purpose
   * - ``chroma``
     - ``klea_utils[chroma]``
     - `Chroma <https://github.com/chroma-core/chroma>`_ support for RAG
   * - ``pgvector``
     - ``klea_utils[pgvector]``
     - `pgvector <https://github.com/pgvector/pgvector-python>`_ support for RAG
   * - ``qdrant``
     - ``klea_utils[qdrant]``
     - `Qdrant <https://github.com/qdrant/qdrant>`_ support for RAG
   * - ``huggingface``
     - ``klea_utils[huggingface]``
     - HuggingFace inference provider for RAG
   * - ``ollama``
     - ``klea_utils[ollama]``
     - Ollama inference provider for RAG
   * - ``anthropic``
     - ``klea_utils[anthropic]``
     - Anthropic provider for RAG (native and custom ``/messages`` endpoints)
   * - ``nicegui``
     - ``klea_utils[nicegui]``
     - NiceGUI web UI frontend
   * - ``full``
     - All vector store and inference provider extras except ``anthropic``
     - All RAG optional extras; Anthropic stays opt-in

Usage::

   pip install klea_rag[full]

klea (WIP: coming soon) and neuroml-mcp (from source)
------------------------------------------------------

``klea`` is under active development and not yet ready for general use.
``neuroml-mcp`` is also in active development.  Neither is on PyPI.
To install them, clone the repository and follow the
:doc:`development workflow <contributing>`.

PyTorch / CUDA (optional)
-------------------------

No Klea package declares PyTorch as a direct dependency, and the correct
torch build depends on your GPU hardware: newer CUDA builds drop kernel
support for older GPUs.  torch is, however, installed transitively by
the document-ingestion extra (``ingest`` -> docling ->
``docling-slim[standard]`` -> ``torch`` + ``torchvision``), which the
``full``/``test``/``dev`` extras all include.  That transitive build is
unpinned and comes from the default index, so it may lack kernels for
your GPU; docling's OCR/layout processing then falls back to the CPU
instead of using the GPU.

If you want a GPU-accelerated build, install it yourself -- see
:file:`requirements-torch.txt` in the repository root for the full
guide, pinned install commands, and a verified example.  Install the
pinned ``torch`` + ``torchvision`` pair (from the same CUDA index)
*before* the Klea requirements: an installed torch that satisfies
docling's ``torch>=2.2.2,<3.0.0`` is left untouched by the installers.
If a wrong build is already present, force-reinstall the pair instead
(see the file).

The short version:

.. list-table::
   :header-rows: 1

   * - GPU compute capability
     - CUDA build to use
   * - 6.0/6.1 (Pascal) and 7.0/7.5 (Volta/Turing)
     - ``cu126`` or ``cu128`` (CUDA 12.8 or earlier)
   * - 8.0+ (Ampere, Ada, Hopper, Blackwell)
     - Any recent build (``cu129``, ``cu130``, ...)

Check your capability with ``torch.cuda.get_device_capability(0)`` and
install the ``torch``/``torchvision`` pair with the full pin so the
package manager cannot pick a different CUDA suffix::

   uv pip install "torch==<version>+cu126" "torchvision==<version>+cu126" \
       --extra-index-url https://download.pytorch.org/whl/cu126

``torchvision`` must come from the same CUDA index as ``torch``; a
mismatch installs silently but breaks ``import torchvision`` at runtime.

To verify the installed build actually computes on your GPU, run
``python scripts/test_torch.py`` from the repository root.  It runs a
real CUDA compute op; ``python -m torch.utils.collect_env`` only prints
a snapshot and reports CUDA as available even when the build lacks
kernels for your GPU.

PyTorch wheels bundle their own CUDA runtime, so a system CUDA toolkit
is not required to run torch.  It is only needed to compile CUDA
extensions yourself, and it must match the wheel's CUDA version.

Configuration
-------------

Both the RAG and Agent packages load configuration from:

1. Model defaults from the environment (shell env vars or an optional env
   file in ``k=v`` format):

   * ``KLEA_RAG_ENV_FILE`` or ``rag.env`` for the RAG system
   * ``KLEA_AGENT_ENV_FILE`` or ``klea_agent.env`` for the Agent system

   The env file is **optional** -- when it is absent, shell environment
   variables and class defaults are used.  The Agent needs no JSON config,
   so a clean machine can run it with the environment alone; the RAG needs
   the JSON config for its store wiring.

2. A JSON configuration file selected by a *profile* name.

   Each JSON config is identified by a profile: ``--profile <name>`` loads
   ``<name>.json``.  The file is looked up in the current directory first,
   then in the per-app config directory (``~/.config/klea-agent/`` for the
   Agent, ``~/.config/klea-rag/`` for the RAG, honoring ``XDG_CONFIG_HOME``).

   The RAG has a default profile (``klea_rag``), so ``klea_rag.json`` is
   loaded when no ``--profile`` is given and is required (the RAG needs its
   store wiring).  The Agent has **no default config file**: without
   ``--profile`` (or ``KLEA_AGENT_APP_CONFIG_FILE``) it runs from the
   built-in ``AppConfig`` defaults plus the environment, and models can be
   set in the web UI.

   Use ``--profile template`` on any CLI to scaffold a ready-to-fill config
   into the current directory (it refuses to overwrite an existing file).

   ``--profile`` takes precedence over the ``KLEA_AGENT_APP_CONFIG_FILE`` /
   ``KLEA_RAG_APP_CONFIG_FILE`` environment variable, which is still honored
   when set in the shell or a deployment (and, as a fallback, as a key in
   the env file).

Model defaults
~~~~~~~~~~~~~~

Default models are selected per *role* through environment variables.  The
exact set of roles is derived from the graph's model declaration, and each
role ``<ROLE>`` maps to an environment variable ``KLEA_<APP>_<ROLE>_MODEL``
(``<APP>`` is ``AGENT`` or ``RAG``).  For example:

* Agent: ``KLEA_AGENT_CHAT_MODEL``, ``KLEA_AGENT_PLAN_MODEL``,
  ``KLEA_AGENT_TOOL_PICKER_MODEL``, ``KLEA_AGENT_GUARD_MODEL``
* RAG: ``KLEA_RAG_CHAT_MODEL``, ``KLEA_RAG_TOOL_PICKER_MODEL``,
  ``KLEA_RAG_EMBEDDING_MODEL``, ``KLEA_RAG_GUARD_MODEL``

Example env file::

   KLEA_RAG_CHAT_MODEL=ollama:qwen3:0.6b
   KLEA_RAG_EMBEDDING_MODEL=ollama:bge-m3

Set these in your shell (``~/.bashrc``, ``~/.zshrc``, or inline on the
command line) or in the env file.  Setting them inline is the easiest way
to try a model without editing any files::

   KLEA_AGENT_CHAT_MODEL=ollama:qwen3:0.6b klea cli

If a required model is not set, the server still starts but logs a warning
listing every model environment variable and its current state.  Queries
then return a clear "No model configured" error.  From the web UI you can
set models at runtime, without restarting the server: open the model dialog
with the settings (gear) icon in the status pane and pick each role's
provider, model and optional custom URL from the
`models.dev <https://models.dev>`_ catalogue (free text is still accepted
for uncatalogued models).  The catalogue is a convenience subset, not
exhaustive: enter any other model -- a local/on-device server (Ollama,
LM Studio), a self-hosted endpoint, or a regional provider -- as free text.
The dialog edits the per-session defaults when
no chat is open and the current chat's overrides otherwise; saving in a
chat also updates the defaults, so new chats inherit them.  A first-run
**Choose models** prompt opens the dialog when setup is incomplete, and
send stays disabled until the required models and API keys are configured.
API keys are provider-scoped and managed from the same dialog (**Manage API
keys**); they are masked in responses and expire after a period unused.  See
:doc:`components/web-ui` for the interface.

Example invocation::

   klea-rag-serve --profile my-config
   KLEA_RAG_ENV_FILE=rag.env klea-rag cli --profile my-config

Choosing models
~~~~~~~~~~~~~~~

Each model provider requires its corresponding :mod:`klea_utils` extra
to be installed::

   # For Ollama:
   pip install klea-utils[ollama]
   # or via klea-rag:
   pip install klea-rag[ollama]

   # For HuggingFace:
   pip install klea-utils[huggingface]

   # For Anthropic (also enables custom /messages endpoints):
   pip install klea-utils[anthropic]

See the `LangChain provider docs
<https://docs.langchain.com/oss/python/integrations/providers/overview>`_
for other providers and their package names.  The needed extras
(``huggingface``, ``ollama``, ``anthropic``) are documented in the extras
tables above.

Model names are prefixed according to their provider:

* ``ollama:<model_name>:<tag>`` for Ollama models
* ``huggingface:<model_id>`` for HuggingFace Inference Providers (the
  hosted API; HuggingFace chooses the provider by default).  The optional
  third segment selects the inference provider (e.g.
  ``huggingface:<model_id>:deepinfra``) or a routing policy
  (``:cheapest``, ``:fastest``, ``:preferred``).  Use
  ``huggingface:<model_id>:local`` to run the model locally instead: the
  weights are downloaded and no API key is needed.  Hosted HuggingFace
  models require the ``HF_TOKEN`` environment variable to be set (see
  `HuggingFace tokens <https://huggingface.co/docs/hub/security-tokens>`_).
* ``custom:<model_name>:<url>`` for endpoints not covered by a native
  provider.  A bare base URL defaults to the OpenAI Chat Completions API
  (e.g. ``custom:Qwen:https://inf01.example.com/v1/``); a full endpoint
  URL instead selects the wire API from its path (e.g. opencode Go serves
  different models on different endpoints):

  * ``.../chat/completions`` -- OpenAI Chat Completions;
  * ``.../responses`` -- OpenAI Responses API;
  * ``.../v1/messages`` -- Anthropic Messages API (requires the
    ``anthropic`` extra).

  ``OPENAI_API_KEY`` supplies the key for every custom surface, including
  Anthropic (where it is copied to ``anthropic_api_key``); an explicit
  per-chat ``api_key`` override takes precedence.
* ``<provider>:<model_name>`` for providers listed in the
  `models.dev <https://models.dev>`_ catalog whose endpoint Klea can resolve
  (for example ``openrouter:qwen/qwen3-coder`` or ``deepseek:deepseek-chat``).
  Klea reads the provider's endpoint from the catalog, so no URL is needed.
  The provider's (or, for gateways that mix surfaces, the model's) wire
  protocol decides the surface: OpenAI Chat Completions (most of the catalog,
  including OpenRouter) and the OpenAI Responses API both use the OpenAI
  provider with ``OPENAI_API_KEY`` (Responses is selected automatically for
  models such as those on OpenCode Go); Anthropic-style endpoints use the
  Anthropic surface (requires the ``anthropic`` extra) with the same
  ``OPENAI_API_KEY`` mapping as ``custom:``.  Providers without a catalog
  endpoint (native SDK providers such as ``groq``/``mistral``) are left to
  LangChain as before, as are ``huggingface:`` and ``anthropic:``, which keep
  their dedicated handling.
* Others (e.g. OpenAI, Anthropic) use their standard model names and
  environment variables as supported by LangChain.

Guard models follow the same format.  The guard role is optional -- set
``KLEA_AGENT_GUARD_MODEL`` / ``KLEA_RAG_GUARD_MODEL`` to an empty value to
skip safety screening entirely.

For the RAG app, the embedding model is required only when a domain
configures vector stores; BM25-only domains need no embedding model.  Note
that vector stores load at startup from the embedding model, so an
embedding model chosen per chat in the web UI cannot enable retrieval for
stores -- set ``KLEA_RAG_EMBEDDING_MODEL`` before starting the server.

Environment variables
~~~~~~~~~~~~~~~~~~~~~

The environment variables Klea reads, and what they control:

.. list-table::
   :header-rows: 1
   :widths: 36 64

   * - Variable
     - Purpose
   * - ``KLEA_AGENT_ENV_FILE`` / ``KLEA_RAG_ENV_FILE``
     - Path to the optional ``k=v`` env file (default ``klea_agent.env`` /
       ``rag.env``).
   * - ``KLEA_AGENT_APP_CONFIG_FILE`` / ``KLEA_RAG_APP_CONFIG_FILE``
     - JSON config file to load; ``--profile`` takes precedence when given.
   * - ``KLEA_<APP>_<ROLE>_MODEL``
     - Per-role model defaults, e.g. ``KLEA_AGENT_CHAT_MODEL``,
       ``KLEA_RAG_EMBEDDING_MODEL`` (see `Model defaults`_).
   * - ``OPENAI_API_KEY``
     - API key for OpenAI, for ``custom:`` endpoints, and for catalog
       providers resolved to the OpenAI or Anthropic surface (see
       `Model defaults`_).
   * - ``ANTHROPIC_API_KEY``
     - API key for the native Anthropic provider.
   * - ``HF_TOKEN``
     - HuggingFace token for HuggingFace models/endpoints.
   * - ``GITHUB_TOKEN``
     - Optional GitHub token for the repository ``github`` tool (higher rate
       limits and private repositories).
   * - ``TAVILY_API_KEY`` / ``EXA_API_KEY`` / ``PARALLEL_API_KEY`` /
       ``FIRECRAWL_API_KEY`` / ``BRAVE_API_KEY`` / ``SERPER_API_KEY``
     - Optional API keys for the bundled ``web_search`` tool.  A key only
       raises that provider's rate limits; the tool works with none set (the
       Tavily, Exa, Parallel and Firecrawl hosted endpoints are keyless,
       while Brave and Serper are used only when keyed).
   * - ``SEMANTIC_SCHOLAR_API_KEY``
     - Optional Semantic Scholar API key for the ``search_papers`` tool.
       Semantic Scholar is only searched when this is set, since its search
       endpoint rate-limits nearly every request without a key.
   * - ``KLEA_LOG_LEVEL``
     - Console log level (level name or number; see `Logging`_).
   * - ``KLEA_TOOL_CALL_TIMEOUT``
     - Per-call wall-clock backstop for tool calls, in seconds (default
       ``900``; ``0`` disables).
   * - ``KLEA_ALLOW_ROOT_TOOLS``
     - Allow Klea-authored tools to run as root; refused by default.
   * - ``KLEA_RUN_COMMAND_MAX_TIMEOUT``
     - Ceiling for ``run_command``'s ``timeout_seconds`` in seconds
       (default ``600``).
   * - ``KLEA_MODELS_DEV_URL``
     - Override the models.dev catalog URL (offline mirror / enterprise
       proxy).
   * - ``KLEA_INGEST_MAILTO``
     - Contact email sent to Crossref and OpenAlex, for their polite pool,
       during store ingestion (DOI resolution) and by the ``search_papers``
       tool, which also sends it to PubMed (NCBI E-utilities).
   * - ``NICEGUI_STORAGE_PATH``
     - Absolute directory for the web client's per-session storage (see
       `Web client user storage`_).
   * - ``RUNNING_IN_DOCKER``
     - Internal: skip graph-diagram export on startup (set by the container
       image).

These are shell/process environment variables.  Values set only in the env
file do **not** reach spawned MCP server subprocesses (which inherit the
process environment), so tool-related variables such as
``KLEA_TOOL_CALL_TIMEOUT``, ``KLEA_ALLOW_ROOT_TOOLS``,
``KLEA_RUN_COMMAND_MAX_TIMEOUT`` and the ``web_search`` provider keys must be
exported in the environment that launches the app.

.. _logging:

Logging
-------

Each Klea application writes its logs to a rotating file (1 MB per file,
5 backups) inside its platform user-data directory:

* Linux: ``~/.local/share/<app>/<app>.log``
* macOS: ``~/Library/Application Support/<app>/<app>.log``
* Windows: ``%LOCALAPPDATA%\<app>\<app>.log``

The file captures DEBUG output for the Klea packages and third-party
libraries, while the console shows INFO for Klea and INFO-or-above for
third-party libraries.  The console level can be changed with the
``KLEA_LOG_LEVEL`` environment variable (a case-insensitive level name such
as ``debug``, or a numeric level) or the shared ``--debug`` flag, which takes
precedence and also exports the variable so spawned child processes inherit
it.  The rotating file always captures DEBUG.  Each CLI uses its own
``<app>`` name:

.. list-table::
   :header-rows: 1

   * - Application
     - Log file name
   * - ``klea-rag`` (RAG server / graph)
     - ``klea-rag/klea-rag.log``
   * - ``klea-rag`` terminal client (REPL)
     - ``klea-rag-tui/klea-rag-tui.log``
   * - ``klea-rag`` web client
     - ``klea-rag-web/klea-rag-web.log``
   * - ``klea-agent`` (Agent server / graph)
     - ``klea-agent/klea-agent.log``
   * - ``klea`` terminal client (REPL)
     - ``klea-agent-tui/klea-agent-tui.log``
   * - ``klea`` web client
     - ``klea-agent-web/klea-agent-web.log``
   * - ``nml-mcp`` (MCP server)
     - ``nml_mcp/nml_mcp.log``

The ``cli`` subcommand's terminal client is a lightweight REPL for quick
testing, not the TUI.  It currently uses the ``-tui`` process identity; the
planned full Textual TUI will take that identity over and the REPL will be
dropped.

``klea-stores-create`` logs to the console only.

See :doc:`troubleshooting` for the full diagnostic checklist (name/path
mismatches, embedding dimension, ``chroma.sqlite3`` location, OCR, metadata
maps, and log locations).

Web client user storage
-----------------------

The NiceGUI web clients (``klea-rag web``, ``klea web``) keep a small
per-browser-session identity file so that a returning browser is linked
back to the same user.  The files are written to a per-app platform
user-data directory:

* Linux: ``~/.local/share/<app>/nicegui/``
* macOS: ``~/Library/Application Support/<app>/nicegui/``
* Windows: ``%LOCALAPPDATA%\<app>\nicegui\``

(honouring ``XDG_DATA_HOME``), and are named
``storage-user-<session-id>.json``.  The ``<app>`` is ``klea-rag-web``
for ``klea-rag web`` and ``klea-agent-web`` for ``klea web``, so the two
frontends do not overlap.

Each file stores only a pointer to server-side state::

    {"user_id": "...", "dark_mode": false, "chat_id": "..."}

The chat history itself lives in the server's session store
(``~/.local/share/<app>/sessions.db``) and is not duplicated here.

These files are **never deleted automatically**.  NiceGUI prunes stale
sessions from its in-memory store but leaves the JSON files on disk, so
they accumulate over time and survive server restarts.  **Do not delete
the per-app ``nicegui/`` directory manually** -- it holds the ``user_id``
pointer (see ``runner.py:1273``) that links the browser to server rows
in ``~/.local/share/<app>/sessions.db`` (see ``api/app.py:56`` /
``sessions_db.py:43``).  Removing it mints a new ``user_id`` and
**orphans** previous chats (they remain in ``sessions.db`` /
``checkpoints.db`` at ``graph/base.py:610`` but
``list_chats(user_id)`` at ``sessions_db.py:116`` no longer finds them).
Only delete ``nicegui/`` **after** you have deleted the session from the
frontend -- use the ``Delete user session`` action (see
``runner.py:692``) which calls ``DELETE /chat/{user_id}`` at
``sessions.py:124`` -> ``delete_user_chats`` at ``sessions_db.py:167``
and ``adelete_thread`` at ``sessions.py:136`` -- when there is nothing
left to orphan.

Deployments that need a custom location (e.g. a persistent volume on
HuggingFace Spaces) can set the single environment variable
``NICEGUI_STORAGE_PATH`` (honoured by ``nicegui/storage.py``) to an
absolute directory, for example ``NICEGUI_STORAGE_PATH=/data/nicegui``.
When set it takes precedence over the per-app default.  The legacy
``.nicegui/`` directory next to the working directory is no longer used;
if it exists from an older install it can be removed after confirming
files have been recreated in the new location.
