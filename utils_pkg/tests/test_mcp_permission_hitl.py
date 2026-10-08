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
    assert payload["requests"][0]["directory"] == "/etc"
    assert payload["requests"][0]["confidence"] == "declared"
    assert payload["requests"][0]["source"] == "checkpaths:path"


def test_payload_custom_question():
    payload = build_permission_payload([_request("/etc")], question="Allow?")
    assert payload["question"] == "Allow?"


def test_resolve_once_session_deny():
    requests = [_request("/etc"), _request("/opt"), _request("/var")]
    response = PermissionResponse(
        decisions=[
            PathDecision(directory="/etc", decision="once"),
            PathDecision(directory="/opt", decision="session"),
            PathDecision(directory="/var", decision="deny"),
        ]
    )
    resolution = resolve_decisions(requests, response)
    assert resolution.allowed_now == ["/etc"]
    assert resolution.allowed_session == ["/opt"]
    assert resolution.denied == ["/var"]
    assert resolution.effective_dirs() == ["/etc", "/opt"]


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
            "decisions": [{"directory": "/etc", "decision": "session"}],
        },
    )
    assert resolution.allowed_session == ["/etc"]


def test_resolve_invalid_response_denies():
    assert resolve_decisions(
        [_request("/etc")], {"decisions": [{"bad": 1}]}
    ).denied == ["/etc"]
    assert resolve_decisions([_request("/etc")], None).denied == ["/etc"]
    assert resolve_decisions([_request("/etc")], "garbage").denied == ["/etc"]
