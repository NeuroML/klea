#!/usr/bin/env python3
"""
Tests for shared tool argument-name helpers.

File: utils_pkg/tests/test_tool_args.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail DOT com>
"""

import pytest
from klea_utils.tool_args import is_path_argument_name, is_uri_argument_name


@pytest.mark.parametrize(
    "name",
    ["path", "working_directory", "output_dir", "input_file", "filepath", "root"],
)
def test_is_path_argument_name(name):
    assert is_path_argument_name(name)


@pytest.mark.parametrize("name", ["url", "content", "command", "source", "output"])
def test_is_not_path_argument_name(name):
    assert not is_path_argument_name(name)


@pytest.mark.parametrize("name", ["url", "source_url", "uri", "link", "href"])
def test_is_uri_argument_name(name):
    assert is_uri_argument_name(name)


@pytest.mark.parametrize("name", ["path", "content", "command", "output"])
def test_is_not_uri_argument_name(name):
    assert not is_uri_argument_name(name)
