#!/usr/bin/env python3
"""
Tests for the markdown bare-URL linkification helper.

File: tests/test_linkify.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail DOT com>
"""

import logging

from klea_utils.ui.linkify import linkify_md

logger = logging.getLogger(__name__)


def test_plain_http_url():
    result = linkify_md("see http://example.com/foo for details")
    assert result == (
        "see [http://example.com/foo](http://example.com/foo) for details"
    )


def test_https_url():
    assert linkify_md("https://example.com") == (
        "[https://example.com](https://example.com)"
    )


def test_elsevier_url_with_parens():
    """URLs with parentheses in the path must be linkified whole."""
    url = "http://refhub.elsevier.com/S0092-8674(23)00850-4/sref-19"
    assert linkify_md(f"see {url} for details") == (f"see [{url}]({url}) for details")


def test_wikipedia_url_with_parens():
    url = "https://en.wikipedia.org/wiki/Foo_(bar)"
    assert linkify_md(f"wiki {url} end") == f"wiki [{url}]({url}) end"


def test_multiple_urls():
    assert linkify_md("http://a.example.com/x and https://b.example.com/y") == (
        "[http://a.example.com/x](http://a.example.com/x) and "
        "[https://b.example.com/y](https://b.example.com/y)"
    )


def test_trailing_prose_paren_left_outside():
    """A prose closing paren after a URL must not become part of it."""
    assert linkify_md("http://example.com/a). Next.") == (
        "[http://example.com/a](http://example.com/a)). Next."
    )


def test_existing_markdown_link_left_untouched():
    assert linkify_md(
        "[text](http://refhub.elsevier.com/S0092-8674(23)00850-4/sref-19) "
        "already linked"
    ) == (
        "[text](http://refhub.elsevier.com/S0092-8674(23)00850-4/sref-19) "
        "already linked"
    )


def test_existing_url_label_link_not_double_linked():
    """A ``[url](url)`` link must not be wrapped a second time."""
    link = "[http://example.com](http://example.com)"
    assert linkify_md(link) == link


def test_markdown_links_and_bare_urls_mixed():
    assert linkify_md(
        "linked [text](http://example.com/x) and bare http://example.com/y"
    ) == (
        "linked [text](http://example.com/x) and bare "
        "[http://example.com/y](http://example.com/y)"
    )


def test_fuzzy_domain():
    assert linkify_md("check github.com") == ("check [github.com](http://github.com)")


def test_fuzzy_email():
    assert linkify_md("mail foo@bar.com") == ("mail [foo@bar.com](mailto:foo@bar.com)")


def test_no_urls_unchanged():
    assert linkify_md("no links here") == "no links here"


def test_empty_string():
    assert linkify_md("") == ""


def test_fenced_code_block_url_left_verbatim():
    """A URL inside a fenced block must not be wrapped as a markdown link."""
    text = "before http://a.example.com\n```\nhttp://b.example.com\n```\nafter"
    out = linkify_md(text)
    assert "[http://a.example.com](http://a.example.com)" in out
    assert "http://b.example.com" in out
    assert "[" not in out.split("```")[1]
    assert "[http://a.example.com](http://a.example.com)" in out


def test_tilde_fence_and_unterminated():
    assert linkify_md("~~~\nhttp://x.example.com\n~~~") == (
        "~~~\nhttp://x.example.com\n~~~"
    )
    # Unterminated fence: remainder is verbatim.
    assert linkify_md("```\nhttp://x.example.com") == "```\nhttp://x.example.com"
