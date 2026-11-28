klea-stores-create
==================

Create stores from documents (vector stores and BM25 stores).

The ``build`` and ``store`` commands accept ``--bm25-store <path>``,
which writes the combined chunked documents to a single pickle that can
be configured as a ``bm25_stores`` entry in the RAG config.  When
``--bm25-store`` is omitted, the corpus is written to
``<collection>.pkl`` in the current directory.

The ``pre-check`` command classifies each PDF by whether it needs OCR
(based on whether it carries an embedded text layer) and, with
``--organise``, copies files into ``ocr/`` and ``no-ocr/`` subdirectories
so you can chunk each with the right ``--ocr`` / ``--no-ocr`` flag.  See
:doc:`../guides/create-and-use-rag` for the worked workflow.

The ``store-lint`` command reviews a stored corpus (the BM25 pickle) with
LLM-free checks and prints a summary, suspicious chunks, and
``--samples`` evenly-spaced windows of contiguous chunks for human
review.  It is printed automatically at the end of ``store`` when a BM25
corpus is written.

Three options deserve special attention:

* ``--collection`` -- the collection name inside the store.  It must
  match the ``name`` of the store's ``vector_stores`` / ``bm25_stores``
  entry in the RAG config file (e.g. ``klea.json``); retrieval looks
  stores up by name, so a mismatch silently returns no results.
* ``--store`` -- for local Chroma stores this points at the store
  folder; the database file inside it is always named
  ``chroma.sqlite3`` (the filename is not configurable), so passing the
  path of an existing file is rejected.  A folder that does not exist
  yet is created.  One Chroma store file can hold several collections,
  so ``--collection`` selects which collection within the file is used.
* ``--bm25-store`` -- path to the combined corpus pickle (see above).

.. typer:: klea_utils.ui.stores_create:app
   :prog: klea-stores-create
   :show-nested:
   :width: 70
   :preferred: text
