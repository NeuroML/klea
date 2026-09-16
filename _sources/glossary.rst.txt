Glossary
========

.. glossary::

   Vector store
      A database that stores text chunks alongside their
      *embeddings* (numerical vectors).  Queries are matched by
      semantic similarity rather than keyword search.

   Embedding
      A numerical representation of a piece of text.  Embeddings
      capture meaning -- similar texts have similar embeddings.

   Chunk
      A short section of a document (typically 200--500 tokens).
      Documents are split into chunks before embedding so that
      retrieval returns precise, relevant passages.

   Collection
      A named group of chunks within a vector store.  One store can
      hold multiple collections (e.g. "nml-docs", "nml-elife").

   Domain
      A knowledge area with its own vector stores, description, and
      optional MCP tool configuration.  Queries are routed to a
      domain by the classifier.

   Guard model
      A small LLM that screens queries for safety before they reach
      the main pipeline.

   Chat model
      The main LLM that classifies queries, generates search
      queries, and writes answers.

   Evaluator
      An LLM call that judges the quality of a generated answer and
      decides whether to accept it, retrieve more context, or
      regenerate.

   MCP server
      A `Model Context Protocol (MCP)
      <https://modelcontextprotocol.io/>`_ server that exposes tools
      the LLM can call live -- e.g. querying a database, validating a
      file, or fetching data from an API.

.. seealso::

   * :doc:`concepts/rag` -- an introduction to retrieval-augmented
     generation
   * :doc:`components/rag` -- how Klea implements it
