Vector stores
=============

Configuration
-------------

.. automodule:: klea_utils.stores.config
   :members:
   :show-inheritance:

Stored metadata schema
----------------------

.. automodule:: klea_utils.stores.metadata
   :members:
   :show-inheritance:

Ingestion
---------

.. autoclass:: klea_utils.stores.ingestion.StoresBuilder
   :members:
   :show-inheritance:

Metadata map linting
--------------------

.. automodule:: klea_utils.stores.map_lint
   :members:
   :show-inheritance:

Retrieval
---------

.. autoclass:: klea_utils.stores.retrieval.base.BaseKleaRetriever
   :members:
   :show-inheritance:

.. autoclass:: klea_utils.stores.retrieval.vs.VSRetriever
   :members:
   :show-inheritance:

.. autoclass:: klea_utils.stores.retrieval.bm25.BM25RetrieverManager
   :members:
   :show-inheritance:

Metadata filters
----------------

.. automodule:: klea_utils.stores.filters
   :members:
   :show-inheritance:

BM25 index
----------

.. autoclass:: klea_utils.stores.langchain_bm25.BM25Retriever
   :members:
   :exclude-members: docs, k, preprocess_func, vectorizer
   :show-inheritance:

Utilities
---------

.. automodule:: klea_utils.stores.utils
   :members:
   :show-inheritance:
