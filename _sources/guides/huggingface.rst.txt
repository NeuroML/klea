Deploying Klea RAG on HuggingFace Spaces
========================================

Overview
--------

Klea RAG is a :doc:`retrieval-augmented generation system <../components/rag>`
that answers questions about your documents.  It can be deployed on `HuggingFace
Spaces <https://huggingface.co/spaces>`_ using the ``sdk: docker``
option, with everything needed (RAG pipeline, tool server, web interface)
running inside a single container.

The fastest way to get your own Klea RAG instance on HuggingFace Spaces
is to fork the existing template and customise it.

.. note::

   The steps below reference HuggingFace-specific procedures (duplicating
   a Space, managing secrets, etc.).  See the `HuggingFace Spaces
   documentation <https://huggingface.co/docs/hub/spaces>`_ for the most
   up-to-date instructions.

Prerequisites
-------------

* A HuggingFace account
* `git-xet <https://huggingface.co/docs/hub/en/xet/index>`_ installed and
  configured locally (see the `git-xet setup guide
  <https://huggingface.co/docs/hub/en/xet/index#setup>`_).  Once
  installed, ``git xet checkout`` (used in Step 2) works automatically.
* (Optional) Docker for testing the image locally:

  .. code-block:: bash

     bash build.sh && docker run -p 7860:7860 klea_rag:latest

Step 1: Duplicate the Space
---------------------------

Go to `NeuroKLEA <https://huggingface.co/spaces/NeuroML/NeuroKLEA>`_
and duplicate it to your own account or organisation.

Step 2: Clone your fork locally
-------------------------------

.. code-block:: bash

   git clone <your-space-url>
   cd <your-space>
   git xet checkout

Use the SSH or HTTPS URL shown in the **Clone** button of your Space
page.  The ``git xet checkout`` step is important --- the vector store databases
are tracked with `git-xet <https://huggingface.co/docs/hub/en/xet/index>`_
on HuggingFace Spaces and will not be usable until the binary blobs
are checked out.

Step 3: Replace the vector stores
---------------------------------

Remove the example stores under ``vector-stores/`` and add your own
ChromaDB databases.  See :doc:`create-and-use-rag` for a
step-by-step guide on creating vector stores with ``klea-stores-create``.

.. note::

   Both the `git-xet <https://huggingface.co/docs/hub/en/xet/index>`_
   tracking in ``.gitattributes`` and the
   ``suggested_hardware: cpu-basic`` setting in ``README.md`` assume
   these databases are small (hundreds of MB at most).  For larger
   stores, consider an external vector store such as Qdrant or PGVector
   and update ``klea_rag.json`` accordingly.

Step 4: Edit ``klea_rag.json``
------------------------------

Update the domain names, descriptions, and vector store paths to match
your content.  The store path format is
``chroma:/app/vector-stores/<your-store-dir>``.

.. code-block:: json

   {
       "general": {
           "default_k": 5,
           "k_max": 10,
           "max_refs_size": 20000,
           "non_domain_chat": true
       },
       "domains": {
           "YourDomain": {
               "description": "What your domain covers",
               "vector_stores": [
                   {
                       "name": "my-store",
                       "path": "chroma:/app/vector-stores/my-store.db"
                   }
               ]
           }
       }
   }

This is a minimal example.  The template's ``klea_rag.json`` contains
additional fields (``fallback_to_training_data``, ``fallback_warning``,
``mcp_servers``) --- keep the ones that are relevant to your use case
and adjust the rest.

Individual vector stores may override the ``general`` retrieval
settings (``default_k``, ``k_max``, ``k_inc``) with their own
per-store values, e.g. a store covering a large corpus can set
``"default_k": 10, "k_max": 25, "k_inc": 5`` on its entry.
``general.max_refs_size`` bounds the total number of reference
characters serialized into the answer LLM's context, independent of
the per-store ``k`` settings.

See :doc:`create-and-use-rag` for a full explanation of
the configuration schema.

Step 5: Edit ``rag.env``
------------------------

Set the chat, embedding, and guard models you want to use.  The
default deployment uses the HuggingFace Inference API:

.. code-block:: ini

   KLEA_RAG_CHAT_MODEL=huggingface:<model-id>:<provider>
   KLEA_RAG_EMBEDDING_MODEL=huggingface:<model-id>
   KLEA_RAG_GUARD_MODEL=huggingface:<model-id>:<provider>

.. seealso::

   :doc:`../install` describes all supported providers (HuggingFace,
   Ollama, OpenAI, Anthropic) and their model naming conventions.

If you use gated HuggingFace models, add a ``HF_TOKEN`` secret
in your Space settings on HuggingFace (Settings > Repository secrets).
The token must have access to the gated model repository.  See the
`HF Spaces secrets documentation
<https://huggingface.co/docs/hub/en/spaces-overview#managing-secrets>`_ for details.

Step 6: Edit ``scripts/docker-deploy.sh``
-----------------------------------------

The entrypoint starts three services in order:

.. code-block:: bash

   # Start your own MCP server (or remove this line)
   nml-mcp &

   # Start the RAG API backend
   klea-rag-serve --host 127.0.0.1 --port 8005 &

   # Start the web frontend (foreground, keeps container alive)
   klea-rag web --title "Your Project Name" --server "http://127.0.0.1:8005"

The template uses ``nml-mcp`` (the NeuroML MCP server) by default.
If you do not need NeuroML-specific tools, replace it with your own
MCP server or remove the line entirely.

.. note::

   ``klea-rag web`` (and ``klea-rag cli``) auto-start a server on the
   local machine when none is running.  In this container the backend
   is started explicitly with ``klea-rag-serve`` above, so the
   frontend's readiness probe simply reuses it.

The ``--title`` flag sets the heading shown in the browser tab and
the NiceGUI page header.  Other flags such as ``--subtitle`` and
``--page-icon`` are available too --- see :doc:`../cli/klea-rag` for
the full reference.

Step 7: Commit and push
-----------------------

.. code-block:: bash

   git add .
   git commit -m "Customise for my project"
   git push

HuggingFace Spaces will detect the push to the default branch, build
the Docker image, and deploy your instance automatically.  You can
watch the build progress in the **Building** tab of your Space page.
Once complete, your RAG is live at
``https://<your-org>-<your-space>.hf.space``.

Files reference
---------------

The template files described here live at ``deployments/huggingface/``
in the Klea monorepo.

.. list-table::
   :header-rows: 1

   * - File / directory
     - Purpose
     - Customise?
   * - ``README.md``
     - HuggingFace Space metadata (title, emoji, hardware tier, tags)
     - Optional
   * - ``Dockerfile``
     - Builds the Docker image (Fedora 44, Python 3.13, uv, installs
       packages via pip)
     - Usually leave as-is
   * - ``rag.env``
     - Model selection (chat, embedding, guard)
     - Yes
   * - ``klea_rag.json``
     - Domain configuration, vector store paths, MCP servers
     - Yes
   * - ``vector-stores/``
     - Pre-built ChromaDB databases
     - Yes --- replace with your own
   * - ``scripts/docker-deploy.sh``
     - Container entrypoint: starts MCP server (``nml-mcp`` by default),
       ``klea-rag-serve``, and ``klea-rag web``
     - Edit ``--title``; replace MCP server if needed
   * - ``build.sh``
     - Helper script for local Docker image builds
     - Optional
   * - ``.gitattributes``
     - `git-xet <https://huggingface.co/docs/hub/en/xet/index>`_ patterns
       for binary file tracking
     - Leave as-is

Architecture
------------

The container runs three services in a single process group:

``nml-mcp``
    The MCP tool server that provides NeuroML-related tools (validation,
    conversion, etc.) to the LLM.  Runs in the background.  Replace with
    your own MCP server if you do not need NeuroML-specific tools.

``klea-rag-serve``
    The FastAPI backend that serves the RAG pipeline.  Listens on
    ``127.0.0.1:8005`` (internal --- not exposed to the internet).
    Runs in the background.

``klea-rag web``
    The NiceGUI frontend that provides the chat interface.  Listens on
    ``0.0.0.0:7860`` (the port HuggingFace Spaces exposes).
    Runs in the foreground as the container's main process.

The startup order is deliberate: the MCP server must be ready before the
RAG server loads its configuration, and the RAG server must be serving
before the frontend connects.

Going further
-------------

Once your Space is deployed, open its URL in a browser and start asking
questions.  See the :doc:`create-and-use-rag` guide for
details on querying the RAG system via the web UI, CLI, or API.

Troubleshooting
---------------

**Vector store files are missing after clone.**
    Run ``git xet checkout`` in your Space clone --- the binary segment
    files are tracked with `git-xet <https://huggingface.co/docs/hub/en/xet/index>`_
    and are not downloaded by default.

**Gated model returns 401 / authorization error.**
    Add a ``HF_TOKEN`` secret in your Space settings (see the
    `HF Spaces secrets docs
    <https://huggingface.co/docs/hub/en/spaces-overview#managing-secrets>`_).
    The token must have access to the gated model repository.

**Space runs out of memory.**
    The free ``cpu-basic`` tier has limited RAM.  Reduce ``k_max`` in
    ``klea_rag.json``, use smaller embedding models, or upgrade to a
    paid hardware tier on HuggingFace.  Reducing ``max_refs_size`` also
    shrinks the reference context sent to the answer LLM.

**Container crashes on startup.**
    Check the Space logs for Python tracebacks.  Common causes: a typo in
    ``klea_rag.json`` (e.g. trailing comma), a missing vector store path,
    or an invalid model name in ``rag.env``.

For other issues (name/path mismatches, empty retrieval, embedding model
pitfalls, OCR, metadata maps, and log locations) see
:doc:`../troubleshooting`.
