#!/usr/bin/env python3
"""
Manual end-to-end scenario catalogue for the Klea agent.

Each task is a single ``klea cli --single-query`` run in its own scratch
workspace.  The suite is for observation first: a task may carry a ``setup``
(to seed disposable files) and an optional ``check`` (deterministic side-effect
assertions), but the primary output is the captured CLI transcript and the
workspace left behind for inspection.

File: e2e/tasks.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail dot com>
"""

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

Setup = Callable[[Path], None]
Check = Callable[[Path, str], None]


@dataclass
class E2ETask:
    """One end-to-end scenario.

    :param id: stable short id (pytest case id, scratch subdirectory name)
    :param query: the single query passed to ``klea cli --single-query``
    :param tags: feature tags; each becomes the pytest marker ``e2e_<tag>``
    :param setup: optional seeding of the scratch workspace before the run
    :param check: optional deterministic assertions after the run; called with
        ``(workspace, cli_output)``
    :param timeout: wall-clock seconds allowed for the CLI subprocess
    :param expect: ``"ok"`` asserts the CLI completed without an error event;
        ``"any"`` runs for observation only (no success assertion)
    """

    id: str
    query: str
    tags: list[str] = field(default_factory=list)
    setup: Setup | None = None
    check: Check | None = None
    timeout: int = 300
    expect: str = "ok"


def _seed_notes(workspace: Path) -> None:
    (workspace / "notes.txt").write_text("first line\n")


def _check_create_file(workspace: Path, _output: str) -> None:
    target = workspace / "hello.txt"
    assert target.exists(), "hello.txt was not created"
    assert "hello world" in target.read_text(), "hello.txt has unexpected content"


def _check_edit_file(workspace: Path, _output: str) -> None:
    text = (workspace / "notes.txt").read_text()
    assert "second line" in text, "notes.txt was not appended"


def _check_run_command(_workspace: Path, output: str) -> None:
    assert "hi" in output, "command output 'hi' not present in the reply"


TASKS: list[E2ETask] = [
    # --- chat floor (RouteDecision answers inline, no tools) ---
    E2ETask(id="chat_hello", query="Hi!", tags=["chat"]),
    E2ETask(id="chat_math", query="What is 2 + 2?", tags=["chat"]),
    # --- single tool actions ---
    E2ETask(
        id="list_files",
        query="List the files in the current directory.",
        tags=["tools"],
    ),
    E2ETask(
        id="read_config",
        query="Read the file klea_agent.json and tell me what it contains.",
        tags=["tools"],
    ),
    E2ETask(
        id="create_file",
        query="Create a file called hello.txt containing the text 'hello world'.",
        tags=["tools"],
        check=_check_create_file,
    ),
    E2ETask(
        id="edit_file",
        query="Append a line 'second line' to notes.txt.",
        tags=["tools"],
        setup=_seed_notes,
        check=_check_edit_file,
    ),
    E2ETask(
        id="run_command",
        query="Run the command `echo hi` and tell me the output.",
        tags=["tools"],
        check=_check_run_command,
    ),
    # --- reasoning step ---
    E2ETask(
        id="reasoning",
        query=(
            "Look at the files in this directory and decide which one is most "
            "important for understanding this project. Explain your decision. "
            "Do not modify anything."
        ),
        tags=["reasoning"],
    ),
    # --- review path (AwaitReview stub auto-approves) ---
    E2ETask(
        id="review",
        query=(
            "Create a plan to add a short README to this directory and wait for "
            "my review before doing anything."
        ),
        tags=["review"],
    ),
    # --- failure / guardrail paths (observe; no strict assertions) ---
    E2ETask(
        id="missing_file",
        query="Read the file definitely_missing.txt and report its contents.",
        tags=["failure"],
    ),
    E2ETask(
        id="no_such_tool",
        query="Use the weather tool to tell me tomorrow's forecast for London.",
        tags=["picker"],
    ),
]
