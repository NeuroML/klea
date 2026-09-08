# AGENTS.md - RAG Package

Generic RAG (Retrieval Augmented Generation) implementation for NeuroML.

## Package Overview

Package: `klea_rag`
CLI entry: `klea-rag`, `klea-rag-serve`

## Development Commands

### Building and Installation
```bash
# Install in development mode
uv pip install -e .

# Install development dependencies
uv pip install -e .[dev]
```

### Linting and Formatting
```bash
# Run ruff for linting and fixing
ruff check . --fix
ruff format .

# Sort imports specifically
ruff check . --select I --fix
```

### Testing
```bash
# Run all tests
pytest

# Run a single test file
pytest tests/test_rag.py

# Run tests with verbose output
pytest -v
```

## Architecture

### Package Structure
```
klea_rag/
├── api/             # RAG API contract (own payload + routers; ADR-0031)
│   ├── main.py      # FastAPI app creation
│   ├── chat.py      # RagPayload + /query[/stream] (via klea_utils chat_core)
│   └── server.py    # Typer serve command
├── config.py        # Configuration loading (env file + JSON)
├── nodes/           # LangGraph nodes for RAG pipeline
│   ├── answer_from_context.py
│   ├── answer_user.py
│   ├── classify_question.py
│   ├── evaluator.py
│   ├── generate_retrieval_query.py
│   ├── init_rag.py
│   ├── retrieve_info.py
│   ├── route_evaluator.py
│   ├── route_query.py
│   ├── tools_caller.py
│   └── tools_picker.py
│   └── prompts/     # Prompt markdown templates per node
├── rag.py           # Main RAG orchestrator (extends BaseLangGraph)
├── schemas.py       # Pydantic schemas
└── ui/
    ├── cli.py       # Typer CLI entry point (klea-rag, klea-rag-serve)
    └── web/         # NiceGUI page composition over klea_utils components (ADR-0031)
        ├── app.py   # process entry point
        └── page.py  # setup_layout -- composes the shared components
```

### Key Technologies
- LangChain for RAG implementation
- LangGraph for orchestration
- Chroma / PGVector / Qdrant for vector store backends
- HuggingFace embeddings
- NiceGUI/Streamlit UI shared from klea_utils

## Code Style

### File Organization
- **Header**: All Python files should have a copyright header
- **Docstrings**: Use reStructuredText format
- **Module structure**: `__init__.py` files should be minimal or empty

### Import Conventions
```python
# 1. Standard library imports
import asyncio
import os
from typing import Any, Dict, List

# 2. Third-party imports
from langchain_core.documents import Document
from langchain_chroma import Chroma
from pydantic import BaseModel

# 3. Local imports
from klea_rag.schemas import RAGRequest
from klea_rag.nodes import retrieve_node
```

### Naming Conventions
- **Functions**: snake_case (`retrieve_documents`, `create_vector_store`)
- **Classes**: PascalCase (`RAGRequest`, `DocumentStore`)
- **Variables**: snake_case (`query`, `documents`, `embedding_model`)
- **Constants**: UPPER_CASE (`DEFAULT_COLLECTION`, `MAX_RESULTS`)

### Vector Store Configuration
- Default collection name should be configurable via environment
- Support both local Chroma and HuggingFace deployments
- Implement proper error handling for connection failures
