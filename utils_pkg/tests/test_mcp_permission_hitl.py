#!/usr/bin/env python3
"""
Tests for the interactive path-approval HITL payload/schema (ADR-0007).

File: utils_pkg/tests/test_mcp_permission_hitl.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail DOT com>
"""

from klea_utils.mcp.path_detect import PathRequest
from klea_utils.mcp.permission_hitl import (
    DEFAULT_QUESTION,
    PathDecision,
    PermissionResponse,
    build_permission_payload,
    resolve_decisions,
)


def _request(directory: str) -> PathRequest:
    return PathRequest(
        tool="read_file",
        argument="path",
        raw=f"{directory}/x",
        path=f"{directory}/x",
        directory=directory,
        confidence="declared",
        source="checkpaths:path",
    )


def test_payload_shape():
    payload = build_permission_payload([_request("/etc")])
    assert payload["kind"] == "permission"
    assert payload["question"] == DEFAULT_QUESTION
    request = payload["requests"][0]
    assert request["directory"] == "/etc"
    assert request["path"] == "/etc/x"
    assert request["directory_display"] == "/etc"
    assert request["path_display"] == "/etc/x"
    assert request["raw_preview"] == "/etc/x"
    assert request["argument_preview"] == ""
    assert request["confidence"] == "declared"
    assert request["source"] == "checkpaths:path"
    assert request["covered_paths"] == ["/etc/x"]
    assert request["covered_paths_display"] == ["/etc/x"]


def test_payload_bounds_long_display_values():
    long_path = "/" + ("a" * 500)
    long_directory = "/" + ("b" * 400)
    request = PathRequest(
        tool="run_command",
        argument="command",
        raw=long_path,
        path=long_path,
        directory=long_directory,
        confidence="guess",
        source="value:command",
        argument_preview="..." + long_path[:100],
    )
    payload = build_permission_payload([request])
    item = payload["requests"][0]

    assert item["path"] == long_path
    assert item["directory"] == long_directory
    assert len(item["path_display"]) < len(long_path)
    assert "chars omitted" in item["path_display"]
    assert len(item["directory_display"]) < len(long_directory)
    assert "chars omitted" in item["directory_display"]
    assert len(item["raw_preview"]) < len(long_path)
    assert "chars omitted" in item["raw_preview"]
    assert item["raw"] == item["raw_preview"]
    assert item["raw_length"] == len(long_path)


def test_payload_includes_all_covered_paths():
    request = PathRequest(
        tool="run_command",
        argument="command",
        raw="/tmp/a",
        path="/tmp/a",
        directory="/tmp",
        confidence="guess",
        source="value:command",
        covered_paths=["/tmp/a", "/tmp/b"],
    )
    item = build_permission_payload([request])["requests"][0]

    assert item["covered_paths"] == ["/tmp/a", "/tmp/b"]
    assert item["covered_paths_display"] == ["/tmp/a", "/tmp/b"]


def test_payload_custom_question():
    payload = build_permission_payload([_request("/etc")], question="Allow?")
    assert payload["question"] == "Allow?"


def test_resolve_once_session_deny():
    requests = [_request("/etc"), _request("/opt"), _request("/var")]
    response = PermissionResponse(
        decisions=[
            PathDecision(key="/etc", decision="once"),
            PathDecision(key="/opt", decision="session"),
            PathDecision(key="/var", decision="deny"),
        ]
    )
    resolution = resolve_decisions(requests, response)
    assert resolution.allowed_now == ["/etc"]
    assert resolution.allowed_session == ["/opt"]
    assert resolution.denied == ["/var"]
    assert resolution.effective_keys() == ["/etc", "/opt"]


def test_resolve_missing_decision_denies():
    resolution = resolve_decisions([_request("/etc")], PermissionResponse())
    assert resolution.denied == ["/etc"]
    assert resolution.allowed_now == []
    assert resolution.allowed_session == []


def test_resolve_cancel_denies_all():
    resolution = resolve_decisions(
        [_request("/etc"), _request("/opt")], PermissionResponse(action="cancel")
    )
    assert resolution.denied == ["/etc", "/opt"]
    assert resolution.allowed_now == []
    assert resolution.allowed_session == []


def test_resolve_accepts_raw_mapping():
    resolution = resolve_decisions(
        [_request("/etc")],
        {
            "action": "answer",
            "decisions": [{"key": "/etc", "decision": "session"}],
        },
    )
    assert resolution.allowed_session == ["/etc"]


def test_resolve_invalid_response_denies():
    assert resolve_decisions(
        [_request("/etc")], {"decisions": [{"bad": 1}]}
    ).denied == ["/etc"]
    assert resolve_decisions([_request("/etc")], None).denied == ["/etc"]
    assert resolve_decisions([_request("/etc")], "garbage").denied == ["/etc"]


def test_resolve_not_a_path_holds_key():
    """A "not a path" decision is dismissed for this dispatch, not denied."""
    resolution = resolve_decisions(
        [_request("/etc")],
        PermissionResponse(decisions=[PathDecision(key="/etc", decision="not_a_path")]),
    )
    assert resolution.not_a_path == ["/etc"]
    assert resolution.allowed_now == []
    assert resolution.allowed_session == []
    assert resolution.denied == []
