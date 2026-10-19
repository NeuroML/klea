#!/usr/bin/env python3
"""
Tests for project-context discovery (AGENTS.md loading).

File: tests/test_discovery.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail dot com>
"""

from klea_agent.discovery import refresh_project_files
from klea_agent.schemas import Discovery, DiscoveryItem


def test_render_empty():
    assert Discovery().render() == "(no project context available)"


def test_render_single_and_multiple():
    discovery = Discovery(
        items=[DiscoveryItem(source="AGENTS.md", content="# Rules\nBe nice.")]
    )
    assert discovery.render() == "### AGENTS.md\n\n# Rules\nBe nice."

    discovery.upsert("README.md", "hi")
    rendered = discovery.render()
    assert "### AGENTS.md" in rendered
    assert "### README.md" in rendered


def test_upsert_replaces_source():
    discovery = Discovery(items=[DiscoveryItem(source="AGENTS.md", content="old")])
    discovery.upsert("AGENTS.md", "new")
    assert len(discovery.items) == 1
    assert discovery.items[0].content == "new"


def test_refresh_reads_agents_md(tmp_path):
    (tmp_path / "AGENTS.md").write_text("project rules")

    discovery = refresh_project_files(Discovery(), tmp_path)

    assert [(i.source, i.content) for i in discovery.items] == [
        ("AGENTS.md", "project rules")
    ]


def test_refresh_prefers_agents_over_claude(tmp_path):
    (tmp_path / "AGENTS.md").write_text("agents")
    (tmp_path / "CLAUDE.md").write_text("claude")

    discovery = refresh_project_files(Discovery(), tmp_path)

    assert discovery.items[0].source == "AGENTS.md"
    assert discovery.items[0].content == "agents"


def test_refresh_no_file_returns_unchanged(tmp_path):
    discovery = Discovery()
    assert refresh_project_files(discovery, tmp_path) is discovery


def test_refresh_no_file_clears_previous_root(tmp_path):
    """A root with no instruction file drops the previous root's item."""
    first = tmp_path / "first"
    first.mkdir()
    (first / "AGENTS.md").write_text("first rules")
    discovery = refresh_project_files(Discovery(), first)
    assert discovery.items

    second = tmp_path / "second"
    second.mkdir()
    cleared = refresh_project_files(discovery, second)

    assert cleared is not discovery
    assert cleared.items == []
    assert cleared.timestamp == 0


def test_refresh_switching_source_drops_stale_item(tmp_path):
    """The current root's file (CLAUDE.md) replaces a stale AGENTS.md item."""
    (tmp_path / "CLAUDE.md").write_text("claude rules")
    discovery = Discovery(
        items=[DiscoveryItem(source="AGENTS.md", content="old rules")],
        timestamp=123,
    )

    refreshed = refresh_project_files(discovery, tmp_path)

    assert [(i.source, i.content) for i in refreshed.items] == [
        ("CLAUDE.md", "claude rules")
    ]


def test_refresh_skips_when_mtime_unchanged(tmp_path):
    (tmp_path / "AGENTS.md").write_text("v1")
    discovery = refresh_project_files(Discovery(), tmp_path)

    # Mutate the cached item; an unchanged-mtime refresh must not clobber it.
    discovery.items[0].content = "sentinel"
    again = refresh_project_files(discovery, tmp_path)

    assert again is discovery
    assert again.items[0].content == "sentinel"
