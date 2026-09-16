Troubleshooting
===============

This page collects common pitfalls for Klea RAG.  It is linked from the
RAG guide, install guide, and deployment guide so you can find it
from any entry point.  For log file locations see :ref:`logging` in
:doc:`install`; for store-creation diagnostics see
:doc:`cli/klea-stores-create`.

Vector stores
-------------

**Name or path mismatch -- silent empty results**

Retrieval looks stores up by the ``name`` field, so the ``--collection``
name passed to ``klea-stores-create build`` must exactly match the
``name`` in the JSON config's ``vector_stores`` / ``bm25_stores`` entry,
and the ``path`` must match what was passed to ``--store`` / the
``--bm25-store`` pickle location.  A mismatch or typo means that store
is never queried and no error is raised -- the LLM simply cites fewer
sources.  Compare with ``klea-stores-create --help`` and the config file.

**Chroma store folder vs file**

For Chroma, ``path`` must point at the *folder* (e.g.
``chroma:/path/to/my-store``), not at the database file itself.  The
file inside that folder is always named ``chroma.sqlite3`` and is not
configurable.  If you point at the file or rename it, the server starts
but retrieval fails with ``no such table`` or empty results.

**Embedding dimension mismatch**

Changing ``KLEA_RAG_EMBEDDING_MODEL`` (or ``--model`` at store-creation
time) to a model with a different embedding dimension invalidates
existing vector stores built with the previous model.  Rebuild the
stores with the new embedding model (``klea-stores-create build
--force``) or revert the model.  A BM25-only domain needs no embedding
model at all.

**Single Chroma file holds many collections**

One ``chroma.sqlite3`` file can hold several collections.  ``--collection``
selects the collection within the file.  Qdrant and pgvector do not
share this model -- each collection is a separate endpoint.  Use
``qdrant:http://...`` or ``pgvector:postgresql://...`` URIs for those
backends.

**Raising ``default_k`` / ``k_max`` brings no more useful chunks**

``k`` and ``k_max`` control how many candidates each store fetches, but
the fused results are then truncated to the global ``max_refs_size``
character budget (:doc:`guides/create-and-use-rag`).  If chunks are
small and many are needed, raising ``k`` helps only while budget
remains.  Increase ``max_refs_size`` or reduce per-chunk tokens.

Queries
-------

**Empty or irrelevant results / low grounding**

Check in order: (1) ``default_k`` / ``k_max`` too low, (2)
``max_refs_size`` truncating, (3) store ``name``/``path`` mismatch above,
(4) source files not in a Docling-supported format, (5) hybrid retrieval
not enabled where exact symbols matter (add a ``bm25_stores`` entry and
merge results with RRF -- see :doc:`components/rag`).  Use
``_source_scores`` (vector cosine in ``[0, 1]`` vs BM25 raw score) and
``rerank_by_recency`` recency bias only as a tie-breaker.

**Filters appear not to apply**

Domain ``filter_fields`` are LLM-generated from natural language -- there
is no CLI flag.  A query like ``papers by Sinha 2020-2025 on NeuroML``
may produce ``{authors: "Sinha", year: {"$gte": 2020, "$lte": 2025}}``
only when that domain declares those fields (``value_type`` ``string``
vs ``list`` matters).  A declared field only works when the underlying
stores actually carry that metadata key; see the metadata enrichment notes
in :doc:`guides/create-and-use-rag` and the extraction cascade in
:doc:`components/rag`.  Person-name fields match partial names via
per-word variants (``Ankur Sinha`` matches ``Sinha``).

Models and servers
------------------

**Ollama is not running / model not found**

Start with ``ollama serve`` or as a system service.  Pull all three
roles (chat, guard, embedding) e.g. ``ollama pull qwen3:0.6b``,
``ollama pull llama-guard3:1b``, ``ollama pull bge-m3:latest``, then
``ollama list`` to verify tags (``:0.6b`` vs ``:latest``).

**Server starts but queries return ``No model configured``**

A missing required model is not fatal -- the server still starts and
logs a warning listing every ``KLEA_RAG_*_MODEL`` / ``KLEA_AGENT_*_MODEL``
and its state.  Set the missing ``KLEA_*_MODEL`` before the next query
or from the web UI gear icon.  For vector retrieval at startup, the
embedding model must be set **before** ``klea-rag-serve`` starts; a
per-chat embedding choice in the UI cannot enable it.

**Guard model / safety**

Set ``KLEA_RAG_GUARD_MODEL`` / ``KLEA_AGENT_GUARD_MODEL`` to an empty
value to skip safety screening entirely.  The guard runs before
classification, so unsafe queries are declined immediately.

**JSON / env / profile errors**

``KLEA_RAG_ENV_FILE`` must point at a valid ``k=v`` env file; ``--profile
<name>`` must resolve to ``<name>.json`` in the current directory or
``~/.config/klea-rag/`` (honoring ``XDG_CONFIG_HOME``).  Check for
trailing commas or missing quotes -- ``klea-rag-serve`` logs the exact
parse error.  ``--profile template`` scaffolds a fresh config; it
refuses to overwrite an existing file.

Docling / OCR
-------------

**Scanned PDFs too slow or empty**

Use ``klea-stores-create pre-check <folder>`` to triage: it reports
``OCR need`` vs ``no-OCR`` based on embedded text layers and with
``--organise`` copies files into ``ocr/`` and ``no-ocr/`` (merging into
the same collection later).  Pass ``--no-ocr`` for text-layer PDFs.

GPUs with CUDA compute capability < 7.0 (e.g. Quadro P1000) lack Triton
kernels for the Docling layout model.  Force CPU:

.. code-block:: bash

   DOCLING_DEVICE=cpu DOCLING_NUM_THREADS=16 klea-stores-create build ...

``DOCLING_DEVICE=cpu`` selects the CPU path, ``DOCLING_NUM_THREADS``
(default 4, ``OMP_NUM_THREADS`` also honoured) tunes it.  Follow the
pinned ``torch``/``torchvision`` + ``requirements-torch.txt`` guide in
:doc:`install` when you want GPU acceleration.

**Pytorch GPU not used / ``torch.cuda`` reports available but docling stays on CPU**

Verify with ``python scripts/test_torch.py`` (real CUDA op); ``python -m
torch.utils.collect_env`` prints a snapshot even when kernels are
missing.  Install the pinned ``torch``/``torchvision`` pair from the
same CUDA index (``cu126``/``cu128`` for Pascal/Volta/Turing, any recent
for Ampere+) before the Klea extras.

Metadata maps
-------------

**``map-lint`` warnings / ``store`` fails with missing entry**

Copy ``metadata-map.template.json`` from ``<source_dir>/.klea-cache/``
(never edit it in place), review ``DEFAULT`` plus heading-specific
entries, and pass the copy via ``--metadata-map``.  ``klea-stores-create
map-lint <dir>`` flags missing fields, suspicious titles/DOIs,
year/filename mismatches, and whether top-level keys are actual source
filenames.  A source file with no map entry is fatal (the full report
is printed then the command exits non-zero); a map keyed by heading
titles is flagged as stale.  Metadata map keys support heading-chain
inheritance (full chain, suffix, ancestor) -- see :doc:`components/rag`.

After storing, run ``store-lint <corpus.pkl>`` (printed for BM25
automatically) to check for near-empty chunks, missing bibliographic
metadata, and sample windows of contiguous chunks.

Logging and diagnostics
-----------------------

Klea logs to a rotating per-app file (1 MB x 5 backups) at
``~/.local/share/<app>/<app>.log`` (Linux), ``~/Library/Application
Support/<app>/<app>.log`` (macOS), or ``%LOCALAPPDATA%\<app>\<app>.log``
(Windows) -- see :ref:`logging` in :doc:`install` for the full per-CLI
table.  The console shows ``INFO``; the file captures ``DEBUG``.

NiceGUI web clients keep ``storage-user-*.json`` small session
pointers in the per-app user-data ``nicegui/`` directory
(``~/.local/share/<app>/nicegui/`` on Linux, honouring
``NICEGUI_STORAGE_PATH`` when set); they are never auto-deleted.  Do not
remove the ``nicegui/`` directory unless the session has been deleted
from the frontend first (see ``Web client user storage`` in
:doc:`install`) -- otherwise chats are orphaned under the old
``user_id``.

HuggingFace Spaces
------------------

* **Vector store files missing after clone** -- run ``git xet checkout``
  (binary `git-xet` blobs not downloaded by default).
* **Gated model 401** -- add ``HF_TOKEN`` secret in Space settings.
* **OOM on ``cpu-basic``** -- reduce ``k_max`` / ``max_refs_size``, use
  a smaller embedding model, or switch to external Qdrant/pgvector.

See also
--------

* :doc:`guides/create-and-use-rag` -- build, configure, query walkthrough
* :doc:`install` -- dependency extras, model naming, :ref:`logging` table
* :doc:`components/rag` -- pipeline, hybrid retrieval, metadata cascade
* :doc:`cli/klea-stores-create` -- full ingestion CLI reference
* :doc:`guides/huggingface` -- Docker deploy specifics
