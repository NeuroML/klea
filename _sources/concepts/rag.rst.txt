RAG
===

What is RAG?
------------

Retrieval Augmented Generation (RAG) is an AI framework that
retrieves facts from an external knowledge base to ground large
language models (LLMs) on the most accurate, up-to-date information
and to give users insight into LLMs' generative process [Wikipedia]_.

.. [Wikipedia] Retrieval-augmented generation - Wikipedia
   https://en.wikipedia.org/wiki/Retrieval-augmented_generation

In simpler terms: instead of asking the LLM to answer from its
(frozen, possibly outdated) training data alone, you first look up
relevant documents in your own vector store and hand them to the LLM
as context.  The LLM's answer is grounded in those documents, and the
source can be cited -- reducing hallucinations and making the process
transparent.

Why RAG over fine-tuning?
-------------------------

RAG has several advantages over fine-tuning (retraining) a model on
your data:

* **No training cost** -- no GPU needed, no training pipeline.
  RAG works with any LLM out of the box.
* **Incremental** -- add documents any time; no re-training needed.
* **Grounded answers** -- the LLM cites its sources.  Fine-tuned
  models can still hallucinate facts they were trained on.
* **Transparent** -- you control the corpus.  If an answer looks
  wrong, you can inspect the retrieved chunks.
* **Swap models freely** -- change the underlying LLM without
  rebuilding your knowledge base.

Fine-tuning still has its place (teaching a model an entirely new
skill or output format), but for open-ended question answering over a
document collection, RAG is the simpler and more maintainable choice.

How Klea implements it
----------------------

Klea applies these ideas in its :doc:`RAG component <../components/rag>`:
a multi-domain pipeline that classifies each query, retrieves from
configurable vector and keyword stores, answers from the retrieved
context, and evaluates the result.  That page covers the pipeline
stages, domains and stores, hybrid retrieval, and the bibliographic
metadata cascade.
