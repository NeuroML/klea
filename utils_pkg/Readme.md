# Klea Utils

Knowledge vaLidated Expert AI Assistant for scientific research.

This package provides shared infrastructure used by all other Klea
packages: FastAPI app factory (chat, health, sessions, streaming),
configurable LLM setup with runtime model switching, vector store
management (Chroma/PGVector/Qdrant), logging with sensitive-data masking,
and NiceGUI/Streamlit web UIs and TUI.

At its core is `BaseLangGraph`, an extensible framework (Template Method)
for building LangGraph orchestrators: subclass it and reuse Klea's
config/env loading, MCP client, vector and BM25 stores, checkpointing,
streaming, and per-request model switching.

Funded by the [BioFAIR](https://biofair.uk/) Pathfinder project
("Creating AI-enabled analysis pipelines for FAIR neuroscience data").

Documentation: https://klea.science
