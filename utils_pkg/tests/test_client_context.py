#!/usr/bin/env python3
"""
Tests for the runtime client context model and validation.

File: utils_pkg/tests/test_client_context.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail DOT com>
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from klea_utils.client_context import (
    MAX_ENV_BYTES,
    MAX_ENV_VARS,
    ClientContext,
    client_context_fingerprint,
    client_context_from_context,
    validate_client_context,
)
from klea_utils.errors import ClientContextError
from pydantic import ValidationError


def test_model_forbids_unknown_fields():
    with pytest.raises(ValidationError):
        ClientContext.model_validate({"cwd": "/tmp", "mystery": True})


def test_validate_accepts_loopback_context(tmp_path):
    cwd = tmp_path / "project"
    cwd.mkdir()

    context = validate_client_context(
        {
            "cwd": str(cwd),
            "interpreter": sys.executable,
            "env": {"KLEA_TEST": "1"},
        },
        peer_host="127.0.0.1",
    )

    assert context is not None
    assert context.cwd == str(cwd.resolve())
    assert context.interpreter == str(Path(sys.executable).resolve())
    assert context.env == {"KLEA_TEST": "1"}


def test_validate_returns_none_for_missing_or_empty_context():
    assert validate_client_context(None, peer_host="127.0.0.1") is None
    assert validate_client_context({}, peer_host="127.0.0.1") is None


def test_validate_rejects_non_loopback_peer(tmp_path):
    with pytest.raises(ClientContextError, match="loopback"):
        validate_client_context({"cwd": str(tmp_path)}, peer_host="192.168.1.10")


def test_validate_rejects_relative_cwd():
    with pytest.raises(ClientContextError, match="absolute"):
        validate_client_context({"cwd": "relative/path"}, peer_host="127.0.0.1")


def test_validate_rejects_missing_cwd(tmp_path):
    with pytest.raises(ClientContextError, match="does not exist"):
        validate_client_context(
            {"cwd": str(tmp_path / "missing")}, peer_host="127.0.0.1"
        )


def test_validate_rejects_cwd_that_is_a_file(tmp_path):
    file_path = tmp_path / "file.txt"
    file_path.write_text("data")
    with pytest.raises(ClientContextError, match="not a directory"):
        validate_client_context({"cwd": str(file_path)}, peer_host="127.0.0.1")


def test_validate_rejects_relative_interpreter():
    with pytest.raises(ClientContextError, match="absolute"):
        validate_client_context({"interpreter": "python"}, peer_host="127.0.0.1")


def test_validate_rejects_missing_interpreter(tmp_path):
    with pytest.raises(ClientContextError, match="does not exist"):
        validate_client_context(
            {"interpreter": str(tmp_path / "missing-python")},
            peer_host="127.0.0.1",
        )


def test_validate_rejects_non_executable_interpreter(tmp_path):
    interpreter = tmp_path / "python"
    interpreter.write_text("#!/bin/sh\n")
    interpreter.chmod(0o644)
    with pytest.raises(ClientContextError, match="not executable"):
        validate_client_context(
            {"interpreter": str(interpreter)}, peer_host="127.0.0.1"
        )


@pytest.mark.parametrize(
    "env",
    [
        {"": "value"},
        {"BAD=NAME": "value"},
        {"GOOD": "bad\x00value"},
        {"GOOD": 1},
    ],
)
def test_validate_rejects_invalid_env(env):
    with pytest.raises(ClientContextError):
        validate_client_context({"env": env}, peer_host="127.0.0.1")


def test_validate_rejects_too_many_env_vars():
    env = {f"KEY_{i}": "x" for i in range(MAX_ENV_VARS + 1)}
    with pytest.raises(ClientContextError, match="too many"):
        validate_client_context({"env": env}, peer_host="127.0.0.1")


def test_validate_rejects_oversized_env():
    env = {"BIG": "x" * (MAX_ENV_BYTES + 1)}
    with pytest.raises(ClientContextError, match="too large"):
        validate_client_context({"env": env}, peer_host="127.0.0.1")


def test_fingerprint_is_stable_and_order_independent():
    first = ClientContext(
        cwd="/tmp/a", interpreter="/usr/bin/python", env={"A": "1", "B": "2"}
    )
    second = ClientContext(
        cwd="/tmp/a", interpreter="/usr/bin/python", env={"B": "2", "A": "1"}
    )

    assert client_context_fingerprint(first) == client_context_fingerprint(second)
    assert client_context_fingerprint(None) == ""
    assert client_context_fingerprint({}) == ""


def test_fingerprint_changes_with_context():
    base = ClientContext(cwd="/tmp/a", env={"A": "1"})
    changed_env = ClientContext(cwd="/tmp/a", env={"A": "2"})
    changed_cwd = ClientContext(cwd="/tmp/b", env={"A": "1"})

    assert client_context_fingerprint(base) != client_context_fingerprint(changed_env)
    assert client_context_fingerprint(base) != client_context_fingerprint(changed_cwd)


def test_client_context_from_runtime_context():
    context = ClientContext(cwd="/tmp/a")

    assert client_context_from_context(None) is None
    assert client_context_from_context({}) is None
    assert client_context_from_context({"client_context": None}) is None
    assert (
        client_context_from_context(SimpleNamespace(client_context=context)) == context
    )
    assert client_context_from_context({"client_context": {"cwd": "/tmp/a"}}) == context
