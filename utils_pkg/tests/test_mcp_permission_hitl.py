#!/usr/bin/env python3
"""
Tests for the interactive path-approval HITL payload/schema (ADR-0007).

File: utils_pkg/tests/test_mcp_permission_hitl.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail DOT com>
"""

from klea_utils.mcp.path_detect import (
    MAX_STEP_REFS,
    MAX_STEP_SUMMARY_CHARS,
    PathRequest,
    StepRef,
    bounded_step_summary,
)
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


def test_payload_confidence_reason_checkpaths():
    """A checkpaths declaration is explained to the user."""
    item = build_permission_payload([_request("/etc")])["requests"][0]
    assert item["confidence_reason"] == "declared by the tool"


def test_payload_confidence_reason_picker():
    """A picker-supplied path is explained to the user."""
    request = PathRequest(
        tool="some_tool",
        argument="",
        raw="/opt/x",
        path="/opt/x",
        directory="/opt",
        confidence="expected",
        source="picker",
    )
    item = build_permission_payload([request])["requests"][0]
    assert item["confidence_reason"] == "declared by the path picker"


def test_payload_confidence_reason_name_heuristic():
    """A path-like argument name is explained to the user."""
    request = PathRequest(
        tool="t",
        argument="path",
        raw="/opt/x",
        path="/opt/x",
        directory="/opt",
        confidence="guess",
        source="name:path",
    )
    item = build_permission_payload([request])["requests"][0]
    assert item["confidence_reason"] == "path-like argument name"


def test_payload_confidence_reason_value_heuristic():
    """A heuristic match in an argument value is explained to the user."""
    request = PathRequest(
        tool="run_command",
        argument="command",
        raw="cat /opt/x",
        path="/opt/x",
        directory="/opt",
        confidence="guess",
        source="value:command",
    )
    item = build_permission_payload([request])["requests"][0]
    assert item["confidence_reason"] == "heuristic: found in the argument value"


def test_payload_passes_through_preview_offsets():
    """Preview offsets and lengths pass through the payload unchanged."""
    preview = "...cd /tmp/outside.txt;"
    request = PathRequest(
        tool="run_command",
        argument="command",
        raw="cd /tmp/outside.txt;",
        path="/tmp/outside.txt",
        directory="/tmp",
        confidence="guess",
        source="value:command",
        argument_preview=preview,
        preview_token_start=6,
        preview_token_end=22,
        preview_omitted_before=10,
        preview_omitted_after=5,
        argument_length=38,
        token_line=1,
    )
    item = build_permission_payload([request])["requests"][0]

    assert item["argument_preview"] == preview
    assert item["preview_token_start"] == 6
    assert item["preview_token_end"] == 22
    assert item["preview_omitted_before"] == 10
    assert item["preview_omitted_after"] == 5
    assert item["argument_length"] == 38
    assert item["token_line"] == 1
    # The omitted counts reconcile with the full length and the preview length.
    assert item["preview_omitted_before"] + item["preview_omitted_after"] == (
        item["argument_length"] - len(item["argument_preview"])
    )


def _stepped_request(directory: str, steps: list[StepRef]) -> PathRequest:
    """Return a request carrying plan-step context (display only)."""
    return PathRequest(
        tool="read_file",
        argument="path",
        raw=f"{directory}/x",
        path=f"{directory}/x",
        directory=directory,
        confidence="declared",
        source="checkpaths:path",
        steps=steps,
    )


def test_payload_steps_present_when_supplied():
    """A request carrying plan-step context hoists it into the payload."""
    request = _stepped_request(
        "/etc", [StepRef(step_number=1, summary="Read the config")]
    )
    payload = build_permission_payload([request])

    assert payload["steps"] == [{"step_number": 1, "summary": "Read the config"}]
    assert payload["requests"][0]["steps"] == [
        {"step_number": 1, "summary": "Read the config"}
    ]


def test_payload_steps_absent_is_empty():
    """An ask without step context carries an empty ``steps`` list."""
    payload = build_permission_payload([_request("/etc")])

    assert payload["steps"] == []
    assert payload["requests"][0]["steps"] == []


def test_payload_steps_dedupe_by_number_in_order():
    """Step references from several requests merge, ordered by step number."""
    first = _stepped_request("/etc", [StepRef(step_number=2, summary="Second step")])
    second = _stepped_request(
        "/opt",
        [
            StepRef(step_number=1, summary="First step"),
            StepRef(step_number=2, summary="Second step"),
        ],
    )
    payload = build_permission_payload([first, second])

    assert [step["step_number"] for step in payload["steps"]] == [1, 2]


def test_payload_steps_are_capped():
    """The payload lists at most ``MAX_STEP_REFS`` steps."""
    request = _stepped_request(
        "/etc",
        [
            StepRef(step_number=number, summary=f"Step {number}")
            for number in range(1, MAX_STEP_REFS + 5)
        ],
    )
    payload = build_permission_payload([request])

    assert len(payload["steps"]) == MAX_STEP_REFS


def test_bounded_step_summary_bounds_a_long_value():
    """A long step summary is collapsed to one bounded line."""
    bounded = bounded_step_summary("word " * 100)

    assert "\n" not in bounded
    assert bounded.endswith("...")
    assert len(bounded) <= MAX_STEP_SUMMARY_CHARS + len("...")
