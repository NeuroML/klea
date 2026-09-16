Bibliographic metadata extraction (``klea\_utils.biblio``)
==========================================================

Automatic extraction of bibliographic metadata (title, authors,
keywords, DOI, URL) from ingested documents, used to pre-fill the
per-file ``DEFAULT`` entries of ``metadata-map.template.json``.  See
:doc:`../../components/rag` for a description of the extraction cascade.

The modules in this package are reusable utilities: the PDF, regex and
DOI-resolution parts do not depend on Docling and can be used on their
own (e.g. from a bundled tool).

PDF Info dict
-------------

.. automodule:: klea_utils.biblio.pdf
   :members:
   :show-inheritance:

Regex extraction
----------------

.. automodule:: klea_utils.biblio.regex
   :members:
   :show-inheritance:

DOI resolution
--------------

.. automodule:: klea_utils.biblio.doi
   :members:
   :show-inheritance:

Docling structured signals
--------------------------

.. automodule:: klea_utils.biblio.docling
   :members:
   :show-inheritance:

Extraction cascade
------------------

.. automodule:: klea_utils.biblio.extract
   :members:
   :show-inheritance:
