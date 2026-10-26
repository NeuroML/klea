#!/usr/bin/env python3
"""
Tests for shared host helpers.

File: utils_pkg/tests/test_net.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail DOT com>
"""

from __future__ import annotations

import pytest
from klea_utils.net import is_loopback_host


@pytest.mark.parametrize(
    "host",
    ["127.0.0.1", "::1", "::ffff:127.0.0.1", "localhost", "[::1]"],
)
def test_loopback_hosts_are_accepted(host):
    assert is_loopback_host(host)


@pytest.mark.parametrize(
    "host",
    ["192.168.1.10", "10.0.0.1", "example.com", "", None, "testclient"],
)
def test_non_loopback_hosts_are_rejected(host):
    assert not is_loopback_host(host)
