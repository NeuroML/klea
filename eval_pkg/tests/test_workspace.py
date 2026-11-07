"""Filesystem-only tests for :mod:`klea_eval.workspace`.

Every test uses ``tmp_path`` and an explicit ``root`` (or a monkeypatched
environment variable), so nothing depends on ``~`` expansion or on POSIX path
separators.

# Copyright 2026 Ankur Sinha <sanjay DOT ankur AT gmail DOT com>
"""

from __future__ import annotations

from pathlib import Path

import pytest
from klea_eval import workspace
from klea_eval.workspace import (
    RunAlreadyCompleteError,
    RunState,
    WorkspaceError,
)

pytestmark = pytest.mark.eval


def _make_complete_run(root: Path, run_id: str) -> Path:
    """Create a published run directory carrying a ``COMPLETE`` marker."""
    final_dir = workspace.run_dir(run_id, root)
    final_dir.mkdir(parents=True)
    (final_dir / "COMPLETE").write_text("")
    (final_dir / "metrics.jsonl").write_text('{"record_type": "run"}\n')
    return final_dir


# -- workspace root resolution -------------------------------------------


def test_explicit_root_wins_over_environment(monkeypatch, tmp_path):
    env_root = tmp_path / "env-root"
    monkeypatch.setenv(workspace.WORKSPACE_ENV_VAR, str(env_root))

    assert workspace.workspace_root(tmp_path / "explicit") == tmp_path / "explicit"
    assert workspace.workspace_root() == env_root


def test_default_root_is_outside_the_source_tree(monkeypatch):
    monkeypatch.delenv(workspace.WORKSPACE_ENV_VAR, raising=False)

    expected = Path.home() / ".local" / "share" / "klea-eval"
    assert workspace.workspace_root() == expected


def test_workspace_root_creates_nothing(tmp_path):
    root = tmp_path / "never-created"

    assert workspace.workspace_root(root) == root
    assert not root.exists()


# -- path derivation ------------------------------------------------------


def test_run_directory_layout_under_runs(tmp_path):
    assert workspace.runs_dir(tmp_path) == tmp_path / "runs"
    assert workspace.run_dir("abc", tmp_path) == tmp_path / "runs" / "abc"
    assert workspace.run_dir_tmp("abc", tmp_path) == tmp_path / "runs" / "abc.tmp"


def test_metrics_path_is_at_the_run_root(tmp_path):
    run = workspace.run_dir("abc", tmp_path)

    assert workspace.metrics_path(run) == run / "metrics.jsonl"
    assert workspace.metrics_path(run).parent == run
    assert "workspace" not in workspace.metrics_path(run).parts


# -- fresh creation -------------------------------------------------------


def test_prepare_run_creates_the_skeleton(tmp_path):
    tmp_dir = workspace.prepare_run("run-1", root=tmp_path)

    assert tmp_dir == workspace.run_dir_tmp("run-1", tmp_path)
    assert (tmp_dir / "workspace" / "fixtures").is_dir()
    assert (tmp_dir / "workspace" / "outputs").is_dir()
    assert not workspace.run_dir("run-1", tmp_path).exists()
    assert not (tmp_dir / "metrics.jsonl").exists()


def test_prepare_run_uses_the_environment_root(monkeypatch, tmp_path):
    monkeypatch.setenv(workspace.WORKSPACE_ENV_VAR, str(tmp_path))

    tmp_dir = workspace.prepare_run("run-env")

    assert tmp_dir == tmp_path / "runs" / "run-env.tmp"


# -- state detection ------------------------------------------------------


def test_detect_state_new_when_nothing_exists(tmp_path):
    assert workspace.detect_state("absent", tmp_path) is RunState.NEW


def test_detect_state_incomplete_for_leftover_tmp(tmp_path):
    workspace.prepare_run("half", root=tmp_path)

    assert workspace.detect_state("half", tmp_path) is RunState.INCOMPLETE


def test_detect_state_incomplete_for_unmarked_final_directory(tmp_path):
    final_dir = workspace.run_dir("unmarked", tmp_path)
    final_dir.mkdir(parents=True)

    assert not workspace.is_complete(final_dir)
    assert workspace.detect_state("unmarked", tmp_path) is RunState.INCOMPLETE


def test_detect_state_complete_when_marker_present(tmp_path):
    final_dir = _make_complete_run(tmp_path, "done")

    assert workspace.is_complete(final_dir)
    assert workspace.detect_state("done", tmp_path) is RunState.COMPLETE


# -- existing completed run ----------------------------------------------


def test_prepare_run_rejects_a_completed_run(tmp_path, caplog):
    final_dir = _make_complete_run(tmp_path, "done")
    before = sorted(p.name for p in final_dir.iterdir())

    with pytest.raises(RunAlreadyCompleteError):
        workspace.prepare_run("done", root=tmp_path)

    assert workspace.is_complete(final_dir)
    assert sorted(p.name for p in final_dir.iterdir()) == before
    assert not workspace.run_dir_tmp("done", tmp_path).exists()


def test_prepare_run_overwrite_replaces_a_completed_run(tmp_path):
    final_dir = _make_complete_run(tmp_path, "done")
    (final_dir / "stale.txt").write_text("old")

    tmp_dir = workspace.prepare_run("done", root=tmp_path, overwrite=True)

    assert tmp_dir == workspace.run_dir_tmp("done", tmp_path)
    assert tmp_dir.is_dir()
    assert not final_dir.exists()
    assert not (tmp_dir / "stale.txt").exists()


# -- leftover .tmp recovery ----------------------------------------------


def test_prepare_run_discards_a_leftover_tmp(tmp_path, caplog):
    stale_dir = workspace.run_dir_tmp("redo", tmp_path)
    stale_dir.mkdir(parents=True)
    (stale_dir / "stale.txt").write_text("old")

    with caplog.at_level("WARNING", logger="klea_eval.workspace"):
        tmp_dir = workspace.prepare_run("redo", root=tmp_path)

    assert tmp_dir == stale_dir
    assert not (tmp_dir / "stale.txt").exists()
    assert (tmp_dir / "workspace" / "fixtures").is_dir()
    assert (tmp_dir / "workspace" / "outputs").is_dir()
    assert "incomplete run" in caplog.text


def test_discard_run_removes_the_tmp_tree(tmp_path):
    tmp_dir = workspace.prepare_run("scratch", root=tmp_path)

    workspace.discard_run("scratch", root=tmp_path)

    assert not tmp_dir.exists()
    assert workspace.detect_state("scratch", tmp_path) is RunState.NEW


def test_discard_run_is_a_noop_without_a_tmp_tree(tmp_path):
    workspace.discard_run("absent", root=tmp_path)

    assert not workspace.run_dir_tmp("absent", tmp_path).exists()


# -- publication ----------------------------------------------------------


def test_publish_run_renames_and_marks_complete(tmp_path):
    tmp_dir = workspace.prepare_run("publish-me", root=tmp_path)
    metrics = workspace.metrics_path(tmp_dir)
    metrics.write_text('{"record_type": "turn"}\n')

    final_dir = workspace.publish_run("publish-me", root=tmp_path)

    assert final_dir == workspace.run_dir("publish-me", tmp_path)
    assert final_dir.is_dir()
    assert not tmp_dir.exists()

    marker = final_dir / "COMPLETE"
    assert marker.is_file()
    assert marker.stat().st_size == 0
    assert not (final_dir / "workspace" / "COMPLETE").exists()

    assert workspace.metrics_path(final_dir).is_file()
    assert workspace.metrics_path(final_dir).parent == final_dir
    assert workspace.is_complete(final_dir)


def test_publish_run_keeps_the_workspace_skeleton(tmp_path):
    workspace.prepare_run("with-workspace", root=tmp_path)

    final_dir = workspace.publish_run("with-workspace", root=tmp_path)

    assert (final_dir / "workspace" / "fixtures").is_dir()
    assert (final_dir / "workspace" / "outputs").is_dir()


def test_publish_run_refuses_to_clobber_a_published_run(tmp_path):
    workspace.prepare_run("clash", root=tmp_path)
    final_dir = workspace.run_dir("clash", tmp_path)
    final_dir.mkdir(parents=True)

    with pytest.raises(RunAlreadyCompleteError):
        workspace.publish_run("clash", root=tmp_path)

    assert not workspace.is_complete(final_dir)


def test_publish_run_requires_a_tmp_tree(tmp_path):
    with pytest.raises(WorkspaceError):
        workspace.publish_run("never-prepared", root=tmp_path)

    assert not workspace.run_dir("never-prepared", tmp_path).exists()
