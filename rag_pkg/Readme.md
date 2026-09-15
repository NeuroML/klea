# Klea RAG

Knowledge vaLidated Expert AI Assistant for scientific research.

This package provides a generic RAG (Retrieval Augmented Generation)
pipeline with multi-domain support.  It implements a
LangChain/LangGraph state machine for answering queries over your own
documents.  Features:

- multi-domain support with automatic query classification
- vector store retrieval from Chroma, Qdrant, and PGVector backends
- answer evaluation with iterative improvement loops
- MCP tool integration for live data access
- FastAPI server, CLI client, and NiceGUI/Streamlit web UIs

Funded by the [BioFAIR](https://biofair.uk/) Pathfinder project
("Creating AI-enabled analysis pipelines for FAIR neuroscience data").

Documentation: https://neuroklea.org
