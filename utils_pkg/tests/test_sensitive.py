#!/usr/bin/env python3
"""
Tests for the sensitive-file matcher (ADR-0007).

File: utils_pkg/tests/test_sensitive.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail DOT com>
"""

from klea_utils.mcp.sensitive import SENSITIVE_ENV_VAR, is_sensitive


def test_matches_env_files():
    assert is_sensitive(".env")
    assert is_sensitive("project/.env")
    assert is_sensitive(".env.local")
    assert is_sensitive("prod.env")


def test_matches_keys_and_credentials():
    assert is_sensitive("id_rsa")
    assert is_sensitive("server.pem")
    assert is_sensitive("cert.key")
    assert is_sensitive("credentials.json")
    assert is_sensitive("secrets.yaml")
    assert is_sensitive(".netrc")
    assert is_sensitive("service-account.json")


def test_matches_sensitive_directories():
    assert is_sensitive("/home/u/.ssh/id_rsa")
    assert is_sensitive("proj/.aws/credentials")
    assert is_sensitive("x/.kube/config")


def test_ignores_ordinary_files():
    assert not is_sensitive("README.md")
    assert not is_sensitive("src/main.py")
    assert not is_sensitive("notes.txt")


def test_extra_patterns_env(monkeypatch):
    monkeypatch.setenv(SENSITIVE_ENV_VAR, "*.secret, private-*")
    assert is_sensitive("my.secret")
    assert is_sensitive("private-key.txt")
    assert not is_sensitive("public.txt")
