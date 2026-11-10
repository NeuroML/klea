# Klea RAG

Knowledge vaLidated Expert AI Assistant for scientific research.

This package is Klea's retrieval-augmented generation component: a
configurable, multi-domain pipeline built on LangGraph.  Each query is
classified into a domain, retrieved from that domain's stores, answered
from the retrieved context, and evaluated (with optional improve loops).

Features:

- multi-domain support with automatic query classification
- hybrid retrieval over vector stores (Chroma, Qdrant, PGVector) and BM25,
  fused with Reciprocal Rank Fusion and optional cross-encoder reranking
- answer evaluation with iterative improvement loops
- MCP tool integration for live data access
- FastAPI server, Textual terminal UI (`klea-rag` / `klea-rag tui`), and
  NiceGUI web UI (`klea-rag web`)

Funded by the [BioFAIR](https://biofair.uk/) Pathfinder project
("Creating AI-enabled analysis pipelines for FAIR neuroscience data").

Documentation: https://klea.science/components/rag.html
