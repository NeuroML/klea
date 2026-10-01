#!/usr/bin/env python3
"""
Architecture guard: node classes keep no per-run state on the instance.

ADR-0045 moved per-run execution scratch into a per-run ``NodeContext``.
A node's instance attributes are configuration only: they are set once in
``__init__`` (or through a ``@property`` setter) and never mutated by
``execute`` or any hook.  A ``self.<attr> = ...`` assignment elsewhere is
the shared-instance leak class the refactor removed (the old ``_last_*``
fields, and ``SummariseMemoryNode``'s window), so this test fails on it.

File: tests/test_node_state_hygiene.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail DOT com>
"""

import ast
import logging
from pathlib import Path

#: Node classes ultimately derive from these shared bases.
_ROOT_NODE_BASES = {
    "AbstractLangGraphNode",
    "AbstractLLMNode",
    "AbstractRouterNode",
    "BaseLLMNode",
}

#: Methods allowed to assign ``self.<attr>`` (construction-time config).
_ALLOWED_METHODS = {"__init__"}

#: Prefixes for one-shot configuration injectors run before the graph is
#: compiled (for example ``Planner.set_tools_info``).  These configure the
#: node once; they must not hold per-run state.
_ALLOWED_METHOD_PREFIXES = ("set_",)

#: Node module directories, relative to the repository root.
_NODE_DIRS = (
    "utils_pkg/klea_utils/nodes",
    "agent_pkg/klea_agent/nodes",
    "rag_pkg/klea_rag/nodes",
)

_REPO_ROOT = Path(__file__).resolve().parents[2]

logger = logging.getLogger(__name__)


def _base_names(classdef: ast.ClassDef) -> set[str]:
    """Return the unsubscripted base class names of *classdef*."""
    names: set[str] = set()
    for base in classdef.bases:
        if isinstance(base, ast.Name):
            names.add(base.id)
        elif isinstance(base, ast.Subscript) and isinstance(base.value, ast.Name):
            names.add(base.value.id)
        elif isinstance(base, ast.Attribute):
            names.add(base.attr)
    return names


def _is_node(
    name: str, bases: dict[str, set[str]], seen: set[str] | None = None
) -> bool:
    """Return whether *name* derives (transitively) from a node base."""
    seen = seen or set()
    if name in _ROOT_NODE_BASES:
        return True
    if name in seen:
        return False
    seen.add(name)
    return any(_is_node(base, bases, seen) for base in bases.get(name, set()))


def _is_setter(func: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    """Return whether *func* is decorated as a ``@x.setter``."""
    return any(
        isinstance(decorator, ast.Attribute) and decorator.attr == "setter"
        for decorator in func.decorator_list
    )


def _is_allowed_config_method(name: str) -> bool:
    """Return whether *name* is a permitted construction-time config method."""
    return name in _ALLOWED_METHODS or name.startswith(_ALLOWED_METHOD_PREFIXES)


def _self_assignments(func: ast.FunctionDef | ast.AsyncFunctionDef) -> list[str]:
    """Return the attribute names assigned to ``self`` anywhere in *func*."""
    attrs: list[str] = []
    for node in ast.walk(func):
        targets: list[ast.expr] = []
        if isinstance(node, ast.Assign):
            targets = list(node.targets)
        elif isinstance(node, (ast.AnnAssign, ast.AugAssign)):
            targets = [node.target]
        for target in targets:
            if (
                isinstance(target, ast.Attribute)
                and isinstance(target.value, ast.Name)
                and target.value.id == "self"
            ):
                attrs.append(target.attr)
    return attrs


def _find_violations(sources: dict[str, str]) -> list[str]:
    """Return a description for each ``self.<attr> =`` outside construction.

    :param sources: ``{path: source}`` for the modules to scan.
    :returns: Human-readable violation descriptions (empty when clean).
    """
    trees: dict[str, ast.Module] = {
        path: ast.parse(source, filename=path) for path, source in sources.items()
    }
    bases: dict[str, set[str]] = {}
    for tree in trees.values():
        for classdef in ast.walk(tree):
            if isinstance(classdef, ast.ClassDef):
                bases.setdefault(classdef.name, set()).update(_base_names(classdef))

    violations: list[str] = []
    for path, tree in trees.items():
        for classdef in ast.walk(tree):
            if not isinstance(classdef, ast.ClassDef):
                continue
            if not _is_node(classdef.name, bases):
                continue
            for item in classdef.body:
                if not isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                if _is_allowed_config_method(item.name) or _is_setter(item):
                    continue
                for attr in _self_assignments(item):
                    violations.append(
                        f"{path}: {classdef.name}.{item.name} assigns self.{attr}"
                    )
    return violations


def _iter_node_sources() -> dict[str, str]:
    """Return ``{path: source}`` for every module under the node dirs."""
    sources: dict[str, str] = {}
    for rel in _NODE_DIRS:
        node_dir = _REPO_ROOT / rel
        if not node_dir.is_dir():
            continue
        for path in sorted(node_dir.rglob("*.py")):
            if "__pycache__" in path.parts:
                continue
            sources[str(path.relative_to(_REPO_ROOT))] = path.read_text()
    return sources


def test_node_classes_do_not_assign_instance_state_outside_init():
    """A node class mutates ``self`` only in ``__init__`` / property setters."""
    sources = _iter_node_sources()
    assert sources, "no node modules found to scan"
    logger.debug(f"scanned {len(sources)} node modules")

    violations = _find_violations(sources)

    assert not violations, (
        "Node classes must not store per-run state on the instance (ADR-0045): "
        "assign self.<attr> only in __init__ or a @property setter; put per-run "
        "values in the NodeContext.  Violations:\n" + "\n".join(violations)
    )


def test_checker_flags_hook_assignment_and_allows_config():
    """The checker catches a hook assignment but allows init/setter config."""
    clean = """
class Node(BaseLLMNode):
    def __init__(self, value):
        self.value = value

    @property
    def thing(self):
        return self._thing

    @thing.setter
    def thing(self, value):
        self._thing = value

    def set_options(self, options):
        self.options = options
"""
    dirty = """
class Node(BaseLLMNode):
    def __init__(self):
        self.value = 0

    async def execute(self, state):
        self._last_output = state
        return {}
"""
    assert _find_violations({"clean.py": clean}) == []
    violations = _find_violations({"dirty.py": dirty})
    assert len(violations) == 1
    assert "Node.execute assigns self._last_output" in violations[0]
