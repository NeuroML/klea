#!/usr/bin/env python3
"""
Architecture guard: LLM output schemas stay strict-safe and prose-free.

An LLM node's ``output_schema`` (the pydantic model it passes to
``with_structured_output``) reaches the model twice: rendered into the system
prompt as the ``Output schema (strict)`` block, and sent as the provider's
``response_format``.  Two invariants keep that contract safe:

* **No prose.**  Pydantic emits a class docstring as the schema ``description``
  and each ``Field(description=...)`` verbatim, so hand-written prose duplicates
  the node's ``*_system.md`` prompt and can drift from it.  Developer notes
  belong in ``#`` comments (see ``devdocs/system/prompt-conventions.md``,
  Rule 6).
* **Strict-safe.**  A dynamic-key map (``dict[...]``) becomes a JSON object with
  ``additionalProperties``; providers' strict structured-output modes close it
  to ``properties: {}, additionalProperties: false``, so the model is
  constrained to emit ``{}`` (ADR-0044).  Represent dynamic collections as a
  typed array with an identity field instead.

This test discovers every LLM output schema (and every nested model reachable
from one) across the three packages and fails on a class docstring, a
``description=`` keyword, or a ``dict[...]`` field.  The tools picker is
exempt: its output schema is generated per run by
``klea_utils.mcp.call_schema.build_tool_call_schema`` (which has its own
strict-safe tests).

File: tests/test_schema_hygiene.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail DOT com>
"""

import ast
import logging
from pathlib import Path

#: LLM node bases whose second type argument is the output schema.
_OUTPUT_NODE_BASES = {"BaseLLMNode", "AbstractLLMNode"}

#: Nodes whose output schema is built dynamically at run time; exempt from
#: discovery (their generated schema is covered by their own tests).
_DYNAMIC_OUTPUT_NODES = {"ToolsPicker", "ClassifyQuestion"}

#: Package directories scanned for node + schema modules, relative to the repo.
_PACKAGE_DIRS = (
    "utils_pkg/klea_utils",
    "agent_pkg/klea_agent",
    "rag_pkg/klea_rag",
)

#: Annotations that produce a dynamic-key (open) JSON object.
_OPEN_OBJECT_NAMES = {"dict", "Dict"}

_REPO_ROOT = Path(__file__).resolve().parents[2]

logger = logging.getLogger(__name__)


def _base_names(classdef: ast.ClassDef) -> list[ast.expr]:
    """Return the base-class expressions of *classdef*."""
    return list(classdef.bases)


def _output_schema_name(classdef: ast.ClassDef, dynamic_nodes: set[str]) -> str | None:
    """Return the declared output-schema class name for an LLM node.

    Reads ``class X(BaseLLMNode[State, Schema])`` (the second type argument) and
    any ``output_schema=Schema`` keyword in the class body.  Returns ``None``
    for non-LLM classes, dynamic-output nodes, and ``BaseModel``/``None``.
    """
    if classdef.name in dynamic_nodes:
        return None

    candidates: list[str] = []
    for base in _base_names(classdef):
        if (
            isinstance(base, ast.Subscript)
            and isinstance(base.value, ast.Name)
            and base.value.id in _OUTPUT_NODE_BASES
        ):
            slc = base.slice
            elts = slc.elts if isinstance(slc, ast.Tuple) else [slc]
            last = elts[-1]
            if isinstance(last, ast.Name):
                candidates.append(last.id)

    for node in ast.walk(classdef):
        if isinstance(node, ast.Call):
            for kw in node.keywords:
                if (
                    kw.arg == "output_schema"
                    and isinstance(kw.value, ast.Name)
                    and kw.value.id != "None"
                ):
                    candidates.append(kw.value.id)

    for name in candidates:
        if name not in {"BaseModel", "Any", "None"}:
            return name
    return None


def _is_docstring(stmt: ast.stmt) -> bool:
    """Return whether *stmt* is a bare string literal (a docstring)."""
    return (
        isinstance(stmt, ast.Expr)
        and isinstance(stmt.value, ast.Constant)
        and isinstance(stmt.value.value, str)
    )


def _has_description_kwarg(classdef: ast.ClassDef) -> bool:
    """Return whether a class-body assignment uses ``description=``."""
    for stmt in classdef.body:
        if not isinstance(stmt, (ast.AnnAssign, ast.Assign)):
            continue
        value = stmt.value
        if value is None:
            continue
        for node in ast.walk(value):
            if isinstance(node, ast.Call) and any(
                kw.arg == "description" for kw in node.keywords
            ):
                return True
    return False


def _open_object_annotations(classdef: ast.ClassDef) -> list[str]:
    """Return the field names annotated as a dynamic-key ``dict[...]``."""
    names: list[str] = []
    for stmt in classdef.body:
        if not isinstance(stmt, ast.AnnAssign) or not isinstance(stmt.target, ast.Name):
            continue
        for node in ast.walk(stmt.annotation):
            if isinstance(node, ast.Subscript) and _is_open_object(node.value):
                names.append(stmt.target.id)
                break
    return names


def _is_open_object(value: ast.expr) -> bool:
    """Return whether *value* names ``dict``/``Dict``."""
    if isinstance(value, ast.Name):
        return value.id in _OPEN_OBJECT_NAMES
    if isinstance(value, ast.Attribute):
        return value.attr in _OPEN_OBJECT_NAMES
    return False


def _annotation_class_refs(classdef: ast.ClassDef) -> set[str]:
    """Return names referenced in the class's field annotations."""
    refs: set[str] = set()
    for stmt in classdef.body:
        if not isinstance(stmt, ast.AnnAssign):
            continue
        for node in ast.walk(stmt.annotation):
            if isinstance(node, ast.Name):
                refs.add(node.id)
    return refs


def _collect_classdefs(forest: dict[str, ast.Module]) -> dict[str, ast.ClassDef]:
    """Return ``{class name: ClassDef}`` for every class in the parsed modules."""
    classdefs: dict[str, ast.ClassDef] = {}
    for tree in forest.values():
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef):
                classdefs.setdefault(node.name, node)
    return classdefs


def _discover_output_schemas(
    forest: dict[str, ast.Module],
) -> set[str]:
    """Return the names of every LLM output schema and nested model."""
    classdefs = _collect_classdefs(forest)
    seeds: set[str] = set()
    for classdef in classdefs.values():
        name = _output_schema_name(classdef, _DYNAMIC_OUTPUT_NODES)
        if name is not None:
            seeds.add(name)

    resolved: set[str] = set()
    pending = list(seeds)
    while pending:
        name = pending.pop()
        if name in resolved:
            continue
        classdef = classdefs.get(name)
        if classdef is None:
            continue
        resolved.add(name)
        for ref in _annotation_class_refs(classdef):
            if ref in classdefs and ref not in resolved:
                pending.append(ref)
    return resolved


def _find_violations(forest: dict[str, ast.Module]) -> list[str]:
    """Return a description for every output-schema invariant violation.

    :param forest: ``{path: parsed module}`` for the modules to scan.
    :returns: Human-readable violation descriptions (empty when clean).
    """
    classdefs = _collect_classdefs(forest)
    schemas = _discover_output_schemas(forest)
    assert schemas, "no LLM output schemas discovered; discovery is broken"

    violations: list[str] = []
    for name in sorted(schemas):
        classdef = classdefs[name]
        if classdef.body and _is_docstring(classdef.body[0]):
            violations.append(f"{name}: class docstring reaches the LLM schema")
        if _has_description_kwarg(classdef):
            violations.append(f"{name}: Field(description=...) reaches the LLM schema")
        for field in _open_object_annotations(classdef):
            violations.append(
                f"{name}.{field}: dynamic-key dict is a strict-safe violation "
                "(closed to {}); use a typed array with an identity field"
            )
    return violations


def _iter_sources() -> dict[str, str]:
    """Return ``{path: source}`` for every package module (tests excluded)."""
    sources: dict[str, str] = {}
    for rel in _PACKAGE_DIRS:
        package_dir = _REPO_ROOT / rel
        if not package_dir.is_dir():
            continue
        for path in sorted(package_dir.rglob("*.py")):
            if "__pycache__" in path.parts or "build" in path.parts:
                continue
            if "tests" in path.relative_to(package_dir).parts:
                continue
            sources[str(path.relative_to(_REPO_ROOT))] = path.read_text()
    return sources


def _parse(sources: dict[str, str]) -> dict[str, ast.Module]:
    return {path: ast.parse(src, filename=path) for path, src in sources.items()}


def test_llm_output_schemas_are_strict_safe_and_prose_free():
    """No output schema leaks prose or a dynamic-key (open) object."""
    sources = _iter_sources()
    assert sources, "no package modules found to scan"
    logger.debug(f"scanned {len(sources)} modules")

    violations = _find_violations(_parse(sources))

    assert not violations, (
        "LLM output schemas must be strict-safe and prose-free "
        "(see devdocs/system/prompt-conventions.md, Rule 6; ADR-0044).  "
        "Violations:\n" + "\n".join(violations)
    )


def test_checker_flags_prose_and_open_objects():
    """The checker catches a docstring, a description, and a dynamic-key dict."""
    clean = """
class Schema(BaseModel):
    # developer note (comment is fine)
    name: str = Field(default="", ge=0)
    items: list[Item] = Field(default_factory=list)


class Item(BaseModel):
    step_number: int = 0
    reason: str = ""
"""
    dirty = '''
class Schema(BaseModel):
    """A developer docstring."""

    name: str = Field(default="", description="the name")
    mapping: dict[str, int] = Field(default_factory=dict)
'''
    # A minimal LLM node so the schemas are discovered as output schemas.
    node = """
class Node(BaseLLMNode[State, Schema]):
    pass
"""
    clean_forest = _parse({"clean.py": node + clean})
    assert _find_violations(clean_forest) == []

    dirty_forest = _parse({"dirty.py": node + dirty})
    violations = _find_violations(dirty_forest)
    assert any("Schema: class docstring" in v for v in violations)
    assert any("Schema: Field(description=...)" in v for v in violations)
    assert any("Schema.mapping: dynamic-key dict" in v for v in violations)
