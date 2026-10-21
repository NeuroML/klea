#!/usr/bin/env python3
"""
Tests for the shared path permission layer.

File: utils_pkg/tests/test_tools_permission.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail DOT com>
"""

import logging
import sys
from pathlib import Path

import pytest
from klea_utils.mcp.errors import PermissionDeniedError
from klea_utils.mcp.path_detect import (
    MAX_ARGUMENT_PREVIEW_CHARS,
    MAX_PATH_DISPLAY_CHARS,
    MAX_RAW_DISPLAY_CHARS,
    detect_path_requests,
    truncate_middle,
)
from klea_utils.mcp.schemas import ToolInfo
from klea_utils.mcp.tool_impls.permission import (
    check_path_access,
)

logger = logging.getLogger(__name__)


def _root_and_outside(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    outside = tmp_path / "outside"
    return root, outside


def test_allows_inside_project(tmp_path):
    root, _ = _root_and_outside(tmp_path)
    (root / "sub").mkdir()
    check_path_access(root, root)
    check_path_access(root / "sub", root)
    check_path_access(root / "file.txt", root)
    check_path_access(root / "sub" / "deep.txt", root)


def test_allows_root_itself(tmp_path):
    root, _ = _root_and_outside(tmp_path)
    check_path_access(root, root)


def test_denies_outside_project(tmp_path):
    root, outside = _root_and_outside(tmp_path)
    outside.write_text("secret")
    with pytest.raises(PermissionDeniedError):
        check_path_access(outside, root)


def test_denies_sibling(tmp_path):
    root, _ = _root_and_outside(tmp_path)
    other = tmp_path / "other"
    other.mkdir()
    with pytest.raises(PermissionDeniedError):
        check_path_access(other, root)


def test_denies_dotdot_smuggling(tmp_path):
    root, outside = _root_and_outside(tmp_path)
    outside.write_text("secret")
    with pytest.raises(PermissionDeniedError):
        check_path_access(root / ".." / "outside", root)


def test_denies_symlink_escape(tmp_path):
    root, outside = _root_and_outside(tmp_path)
    outside.write_text("secret")
    link = root / "link.txt"
    link.symlink_to(outside)
    with pytest.raises(PermissionDeniedError):
        check_path_access(link, root)


def test_allows_symlink_inside(tmp_path):
    root, _ = _root_and_outside(tmp_path)
    (root / "real.txt").write_text("x")
    link = root / "link.txt"
    link.symlink_to(root / "real.txt")
    check_path_access(link, root)


def test_default_root_is_cwd(tmp_path, monkeypatch):
    root, _ = _root_and_outside(tmp_path)
    monkeypatch.chdir(root)
    check_path_access(root / "sub")
    with pytest.raises(PermissionDeniedError):
        check_path_access(tmp_path)


def test_denies_symlink_loop(tmp_path):
    """A self-referential symlink cannot be resolved, so it is denied."""
    root, _ = _root_and_outside(tmp_path)
    loop = root / "loop"
    loop.symlink_to("loop")

    with pytest.raises(PermissionDeniedError):
        check_path_access(loop, root)


def test_allowed_dirs_permits_outside(tmp_path):
    """A session-approved directory is allowed in addition to the root."""
    root, outside = _root_and_outside(tmp_path)
    outside.mkdir()
    with pytest.raises(PermissionDeniedError):
        check_path_access(outside / "f.txt", root)
    check_path_access(outside / "f.txt", root, allowed_dirs=[str(outside)])


def _root_only(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    return root


def test_truncate_middle_keeps_short_text():
    assert truncate_middle("/etc/hosts") == "/etc/hosts"


def test_truncate_middle_preserves_head_and_tail():
    text = "/home/user/" + ("a" * 500) + "/target.txt"
    preview = truncate_middle(text)
    assert len(preview) < len(text)
    assert preview.startswith("/home/user/")
    assert preview.endswith("/target.txt")
    assert "chars omitted" in preview
    assert len(preview) <= MAX_PATH_DISPLAY_CHARS + 40


def test_detect_keeps_bare_root_guess(tmp_path):
    """A genuine root token is still gated; guessing is deliberately defensive."""
    root = _root_only(tmp_path)
    requests = detect_path_requests(
        "run_command", {"command": "ls /"}, None, project_root=root
    )

    assert len(requests) == 1
    assert requests[0].path == "/"
    assert requests[0].directory == "/"
    assert requests[0].confidence == "guess"
    assert requests[0].source == "value:command"


def test_detect_long_guess_has_bounded_display_context(tmp_path):
    """A long guessed string keeps full values but bounded display previews."""
    root, outside = _root_and_outside(tmp_path)
    token = f"{outside}/{'a' * 1000}"
    requests = detect_path_requests(
        "run_command", {"command": f"echo {token}"}, None, project_root=root
    )

    assert len(requests) == 1
    request = requests[0]
    assert request.path == token
    assert len(request.path) > MAX_PATH_DISPLAY_CHARS
    assert len(request.path_display()) <= MAX_PATH_DISPLAY_CHARS + 40
    assert "chars omitted" in request.path_display()
    assert len(request.raw_preview()) <= MAX_RAW_DISPLAY_CHARS + 40
    assert len(request.argument_preview) <= MAX_ARGUMENT_PREVIEW_CHARS + 10
    assert "..." in request.argument_preview
    assert token[:20] in request.argument_preview


def test_detect_short_argument_preview_is_unchanged(tmp_path):
    root, outside = _root_and_outside(tmp_path)
    info = ToolInfo(checkpaths=["path"])
    requests = detect_path_requests(
        "t", {"path": str(outside / "f")}, info, project_root=root
    )

    assert requests[0].argument_preview == str(outside / "f")


def test_detect_skips_unexpandable_guess(tmp_path, monkeypatch):
    """A guessed ``~`` token must not crash discovery when home is unknown."""
    original = Path.expanduser

    def fake_expanduser(path):
        if str(path).startswith("~"):
            raise RuntimeError("Could not determine home directory.")
        return original(path)

    monkeypatch.setattr(Path, "expanduser", fake_expanduser)

    requests = detect_path_requests(
        "run_command",
        {"command": "cat ~missing/file.txt"},
        project_root=str(tmp_path),
    )

    assert requests == []


def test_detect_ignores_inside_paths(tmp_path):
    root = _root_only(tmp_path)
    (root / "f.txt").touch()
    info = ToolInfo(checkpaths=["path"])
    assert (
        detect_path_requests(
            "read_file", {"path": str(root / "f.txt")}, info, project_root=root
        )
        == []
    )


def test_detect_declared_outside(tmp_path):
    root, outside = _root_and_outside(tmp_path)
    outside.mkdir()
    info = ToolInfo(checkpaths=["path"])
    requests = detect_path_requests(
        "read_file", {"path": str(outside / "x.txt")}, info, project_root=root
    )
    assert len(requests) == 1
    assert requests[0].confidence == "declared"
    assert requests[0].source == "checkpaths:path"
    assert requests[0].directory == str(outside)


def test_detect_picker_paths_expected(tmp_path):
    root, outside = _root_and_outside(tmp_path)
    outside.mkdir()
    requests = detect_path_requests(
        "some_tool", {}, None, project_root=root, picker_paths=[str(outside / "f")]
    )
    assert [request.confidence for request in requests] == ["expected"]
    assert requests[0].source == "picker"


def test_detect_name_heuristic(tmp_path):
    root, outside = _root_and_outside(tmp_path)
    outside.mkdir()
    requests = detect_path_requests(
        "t", {"file_path": str(outside / "f")}, None, project_root=root
    )
    assert len(requests) == 1
    assert requests[0].confidence == "guess"
    assert requests[0].source == "name:file_path"


# https://github.com/NeuroML/klea/issues/204
windows_shell_paths = pytest.mark.skipif(
    sys.platform == "win32",
    reason="Windows paths in shell commands are not detected (#204)",
)


@windows_shell_paths
def test_detect_shell_command_tokens(tmp_path):
    root, outside = _root_and_outside(tmp_path)
    outside.mkdir()
    command = f"cat {outside}/a.txt && echo done"
    requests = detect_path_requests(
        "run_command", {"command": command}, None, project_root=root
    )
    assert len(requests) == 1
    assert requests[0].directory == str(outside)
    assert requests[0].confidence == "guess"
    assert requests[0].source == "value:command"


@windows_shell_paths
def test_detect_shell_redirection_assignment_and_flags(tmp_path):
    root, outside = _root_and_outside(tmp_path)
    outside.mkdir()
    command = f"tool -o --out={outside}/log FOO={outside}/env 2>{outside}/err"
    requests = detect_path_requests(
        "run_command", {"command": command}, None, project_root=root
    )
    assert {request.directory for request in requests} == {str(outside)}


def test_detect_skips_urls_and_globs(tmp_path):
    root = _root_only(tmp_path)
    command = "curl https://example.com/x && ls *.py && rg 'a/b'"
    assert (
        detect_path_requests(
            "run_command", {"command": command}, None, project_root=root
        )
        == []
    )


def test_detect_dedup_keeps_strongest(tmp_path):
    root, outside = _root_and_outside(tmp_path)
    outside.mkdir()
    info = ToolInfo(checkpaths=["path"])
    requests = detect_path_requests(
        "t", {"path": str(outside / "f")}, info, project_root=root
    )
    assert len(requests) == 1
    assert requests[0].confidence == "declared"


def test_detect_allowed_dirs_suppresses(tmp_path):
    root, outside = _root_and_outside(tmp_path)
    outside.mkdir()
    info = ToolInfo(checkpaths=["path"])
    assert (
        detect_path_requests(
            "t",
            {"path": str(outside / "f")},
            info,
            project_root=root,
            allowed_dirs=[str(outside)],
        )
        == []
    )


@windows_shell_paths
def test_detect_file_uri_is_a_path(tmp_path):
    """A ``file:`` URI names a local path and must be checked, not skipped."""
    root, outside = _root_and_outside(tmp_path)
    outside.mkdir()
    requests = detect_path_requests(
        "run_command",
        {"command": f"cat file://{outside}/a.txt"},
        None,
        project_root=root,
    )
    assert len(requests) == 1
    assert requests[0].directory == str(outside)


def test_detect_file_uri_without_authority(tmp_path):
    """``file:/etc/passwd`` (no ``//``) is still unwrapped and checked."""
    root = _root_only(tmp_path)
    requests = detect_path_requests(
        "t",
        {"path": "file:/etc/passwd"},
        ToolInfo(checkpaths=["path"]),
        project_root=root,
    )
    assert len(requests) == 1
    # A drive-less "/etc" resolves on the project's drive on Windows.
    assert requests[0].directory == str((Path(root.anchor) / "etc").resolve())


def test_detect_http_url_still_skipped(tmp_path):
    root = _root_only(tmp_path)
    assert (
        detect_path_requests(
            "run_command",
            {"command": "curl https://example.com/etc/passwd"},
            None,
            project_root=root,
        )
        == []
    )


def test_detect_dotdot_smuggling(tmp_path):
    root = _root_only(tmp_path)
    (tmp_path / "secret.txt").touch()
    requests = detect_path_requests(
        "t",
        {"path": str(root / ".." / "secret.txt")},
        ToolInfo(checkpaths=["path"]),
        project_root=root,
    )
    assert requests
    assert requests[0].directory == str(tmp_path)


def test_detect_sensitive_inside_root(tmp_path):
    """A sensitive file inside the root is a request when include_sensitive."""
    root = _root_only(tmp_path)
    env = root / ".env"
    env.write_text("K=1")
    requests = detect_path_requests(
        "read_file",
        {"path": ".env"},
        ToolInfo(checkpaths=["path"]),
        project_root=root,
        include_sensitive=True,
    )
    assert len(requests) == 1
    assert requests[0].kind == "sensitive"
    assert requests[0].approval_key == str(env.resolve())


def test_detect_sensitive_skipped_without_flag(tmp_path):
    root = _root_only(tmp_path)
    (root / ".env").write_text("K=1")
    assert (
        detect_path_requests(
            "read_file",
            {"path": ".env"},
            ToolInfo(checkpaths=["path"]),
            project_root=root,
        )
        == []
    )


def test_detect_sensitive_allowed_files_suppresses(tmp_path):
    root = _root_only(tmp_path)
    env = root / ".env"
    env.write_text("K=1")
    assert (
        detect_path_requests(
            "read_file",
            {"path": ".env"},
            ToolInfo(checkpaths=["path"]),
            project_root=root,
            include_sensitive=True,
            allowed_files=[str(env)],
        )
        == []
    )


def test_detect_sensitive_bare_token_in_command(tmp_path):
    """A bare sensitive filename in a command is caught without a separator."""
    root = _root_only(tmp_path)
    (root / ".env").write_text("K=1")
    requests = detect_path_requests(
        "run_command",
        {"command": "cat .env"},
        None,
        project_root=root,
        include_sensitive=True,
    )
    assert [request.kind for request in requests] == ["sensitive"]


def test_detect_outside_beats_sensitive(tmp_path):
    """A sensitive-looking file outside the root is an outside request."""
    root, outside = _root_and_outside(tmp_path)
    outside.mkdir()
    requests = detect_path_requests(
        "read_file",
        {"path": str(outside / ".env")},
        ToolInfo(checkpaths=["path"]),
        project_root=root,
        include_sensitive=True,
    )
    assert requests[0].kind == "outside"
