#!/usr/bin/env python3
"""
Tests for the academic paper search sources and the search_papers tool.

File: utils_pkg/tests/test_papers_search.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail DOT com>
"""

import itertools
import json
import time
from types import SimpleNamespace

import httpx
import pytest
from klea_utils.mcp.tool_impls.papers import arxiv, pubmed, search, semantic_scholar
from klea_utils.mcp.tool_impls.papers.arxiv import ARXIV_QUERY_URL
from klea_utils.mcp.tool_impls.papers.crossref import CROSSREF_WORKS_URL
from klea_utils.mcp.tool_impls.papers.europepmc import EUROPEPMC_SEARCH_URL
from klea_utils.mcp.tool_impls.papers.http import Throttle
from klea_utils.mcp.tool_impls.papers.openalex import OPENALEX_WORKS_URL
from klea_utils.mcp.tool_impls.papers.pubmed import (
    PUBMED_EFETCH_URL,
    PUBMED_ESEARCH_URL,
)
from klea_utils.mcp.tool_impls.papers.record import (
    MAX_ABSTRACT_CHARS,
    PaperRecord,
    clean_doi,
    is_preprint,
)
from klea_utils.mcp.tool_impls.papers.search import search_papers
from klea_utils.mcp.tool_impls.papers.semantic_scholar import (
    SEMANTIC_SCHOLAR_SEARCH_URL,
)


class _FakeResponse:
    """Minimal httpx-like response carrying a JSON payload or a text body."""

    def __init__(self, payload=None, status: int = 200, text: str | None = None):
        self._payload = payload
        self.status_code = status
        self.text = text if text is not None else json.dumps(payload)

    def json(self):
        if self._payload is None:
            raise ValueError("not JSON")
        return self._payload


class _FakeSession:
    """Serves canned responses per URL and records the calls made.

    ``routes`` maps a URL to a :class:`_FakeResponse`, to an exception that
    is raised on access, or to a callable ``(params) -> _FakeResponse``.
    """

    def __init__(self, routes: dict):
        self._routes = routes
        self.calls: list[tuple[str, dict]] = []

    async def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        route = self._routes.get(url)
        if route is None:
            raise AssertionError(f"no route registered for {url}")
        if isinstance(route, Exception):
            raise route
        if callable(route):
            return route(kwargs.get("params"))
        return route

    def stream(self, method, url, **kwargs):
        raise AssertionError("stream is not used by the paper search")

    def urls(self) -> list[str]:
        return [url for url, _ in self.calls]


@pytest.fixture(autouse=True)
def _hermetic(monkeypatch):
    monkeypatch.delenv("SEMANTIC_SCHOLAR_API_KEY", raising=False)
    monkeypatch.delenv("KLEA_INGEST_MAILTO", raising=False)
    # Start every test with OpenAlex as the primary general source.
    monkeypatch.setattr(search, "_ROTATION", itertools.count())
    # No real waiting between throttled requests.
    monkeypatch.setattr(arxiv, "_THROTTLE", Throttle(min_interval=0.0))
    monkeypatch.setattr(semantic_scholar, "_THROTTLE", Throttle(min_interval=0.0))
    monkeypatch.setattr(pubmed, "_THROTTLE", Throttle(min_interval=0.0))


def _openalex_work(title="OpenAlex paper", doi="https://doi.org/10.1/oa"):
    return {
        "title": title,
        "doi": doi,
        "publication_year": 2002,
        "authorships": [{"author": {"display_name": "John Guckenheimer"}}],
        "primary_location": {
            "landing_page_url": "https://example.org/oa",
            "source": {"display_name": "SIAM J. Appl. Dyn. Syst."},
        },
        "abstract_inverted_index": {"Chaos": [0], "appears": [1]},
    }


def _crossref_item(title="Crossref paper", abstract="<jats:p>An abstract</jats:p>"):
    item = {
        "DOI": "10.1/cr",
        "title": [title],
        "author": [{"given": "Alan", "family": "Hodgkin"}],
        "issued": {"date-parts": [[1952, 8]]},
        "container-title": ["J. Physiol."],
        "URL": "https://doi.org/10.1/cr",
    }
    if abstract:
        item["abstract"] = abstract
    return item


def _openalex(works):
    return _FakeResponse({"results": works})


def _crossref(items):
    return _FakeResponse({"message": {"items": items}})


ARXIV_FEED = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom" xmlns:arxiv="http://arxiv.org/schemas/atom">
  <entry>
    <id>http://arxiv.org/abs/1811.00173v2</id>
    <title>Structure-preserving numerical
      integrators</title>
    <summary>Motivated by the Hodgkin-Huxley model.</summary>
    <published>2018-11-01T01:01:37Z</published>
    <author><name>Zhengdao Chen</name></author>
    <author><name>Ari Stern</name></author>
    <link href="https://arxiv.org/abs/1811.00173v2" rel="alternate" type="text/html"/>
    <arxiv:doi>10.1137/18M123390X</arxiv:doi>
  </entry>
</feed>
"""

PUBMED_XML = """<?xml version="1.0" ?>
<PubmedArticleSet>
  <PubmedArticle>
    <MedlineCitation>
      <PMID>111</PMID>
      <Article>
        <Journal>
          <JournalIssue><PubDate><MedlineDate>1998 Dec-1999 Jan</MedlineDate></PubDate></JournalIssue>
          <Title>Biophysical journal</Title>
        </Journal>
        <ArticleTitle>Second by relevance</ArticleTitle>
        <AuthorList><Author><CollectiveName>Neuro Consortium</CollectiveName></Author></AuthorList>
      </Article>
    </MedlineCitation>
    <PubmedData><ArticleIdList><ArticleId IdType="pubmed">111</ArticleId></ArticleIdList></PubmedData>
  </PubmedArticle>
  <PubmedArticle>
    <MedlineCitation>
      <PMID>222</PMID>
      <Article>
        <Journal>
          <JournalIssue><PubDate><Year>2012</Year><Month>Jun</Month></PubDate></JournalIssue>
          <Title>The Journal of physiology</Title>
        </Journal>
        <ArticleTitle>Sodium channels at <i>60</i></ArticleTitle>
        <ELocationID EIdType="doi">10.1113/jphysiol.2011.224204</ELocationID>
        <Abstract>
          <AbstractText Label="BACKGROUND">Some background.</AbstractText>
          <AbstractText Label="RESULTS">Some results.</AbstractText>
        </Abstract>
        <AuthorList><Author><LastName>Catterall</LastName><ForeName>William A</ForeName></Author></AuthorList>
      </Article>
    </MedlineCitation>
    <PubmedData><ArticleIdList><ArticleId IdType="pubmed">222</ArticleId></ArticleIdList></PubmedData>
  </PubmedArticle>
</PubmedArticleSet>
"""


def _pubmed_search(ids):
    return _FakeResponse({"esearchresult": {"count": str(len(ids)), "idlist": ids}})


async def test_openalex_parsing_keeps_full_doi_and_filters_journals():
    work = _openalex_work(doi="https://doi.org/10.1088/0957-4484/24/38/383001")
    session = _FakeSession({OPENALEX_WORKS_URL: _openalex([work])})

    result = await search_papers(session, "hodgkin huxley")

    assert result["error"] == ""
    [paper] = result["results"]
    assert paper["source"] == "openalex"
    assert paper["peer_reviewed"] is True
    assert paper["doi"] == "10.1088/0957-4484/24/38/383001"
    assert paper["authors"] == ["John Guckenheimer"]
    assert paper["journal"] == "SIAM J. Appl. Dyn. Syst."
    assert paper["abstract"] == "Chaos appears"
    assert paper["url"] == "https://example.org/oa"
    params = session.calls[0][1]["params"]
    assert params["filter"] == "type:article,primary_location.source.type:journal"
    assert params["search"] == "hodgkin huxley"


async def test_crossref_parsing_strips_abstract_tags(monkeypatch):
    monkeypatch.setattr(search, "_ROTATION", itertools.count(1))
    session = _FakeSession({CROSSREF_WORKS_URL: _crossref([_crossref_item()])})

    result = await search_papers(session, "hodgkin huxley")

    [paper] = result["results"]
    assert paper["source"] == "crossref"
    assert paper["abstract"] == "An abstract"
    assert paper["authors"] == ["Alan Hodgkin"]
    assert paper["year"] == 1952
    assert session.calls[0][1]["params"]["filter"] == "type:journal-article"


async def test_crossref_drops_ssrn_preprints(monkeypatch):
    monkeypatch.setattr(search, "_ROTATION", itertools.count(1))
    ssrn = _crossref_item(title="SSRN preprint")
    ssrn["DOI"] = "10.2139/ssrn.3193693"
    ssrn["container-title"] = ["SSRN Electronic Journal"]
    session = _FakeSession(
        {CROSSREF_WORKS_URL: _crossref([ssrn, _crossref_item(title="Journal paper")])}
    )

    result = await search_papers(session, "hodgkin huxley")

    assert [p["title"] for p in result["results"]] == ["Journal paper"]


async def test_mailto_is_sent_when_set(monkeypatch):
    monkeypatch.setenv("KLEA_INGEST_MAILTO", "someone@example.org")
    session = _FakeSession({OPENALEX_WORKS_URL: _openalex([_openalex_work()])})

    await search_papers(session, "hodgkin huxley")

    assert session.calls[0][1]["params"]["mailto"] == "someone@example.org"


async def test_europepmc_peer_reviewed_and_preprints():
    def _route(params):
        if "SRC:MED" in params["query"]:
            return _FakeResponse(
                {
                    "resultList": {
                        "result": [
                            {
                                "id": "1",
                                "source": "MED",
                                "doi": "10.1/med",
                                "title": "Journal paper",
                                "authorList": {"author": [{"fullName": "Korngreen A"}]},
                                "pubYear": "2026",
                                "journalInfo": {
                                    "journal": {"title": "PLoS Comput Biol"}
                                },
                                "abstractText": "Some <i>abstract</i>",
                            }
                        ]
                    }
                }
            )
        return _FakeResponse(
            {
                "resultList": {
                    "result": [
                        {
                            "id": "PPR1",
                            "source": "PPR",
                            "title": "A preprint",
                            "authorString": "Zhang Y, Han D.",
                            "pubYear": "2026",
                            "bookOrReportDetails": {"publisher": "bioRxiv"},
                            "abstractText": "Preprint abstract",
                        }
                    ]
                }
            }
        )

    session = _FakeSession({EUROPEPMC_SEARCH_URL: _route})

    result = await search_papers(
        session, "hodgkin huxley", domain="life-sciences", preprints=True
    )

    journal, preprint = result["results"]
    assert journal["peer_reviewed"] is True
    assert journal["journal"] == "PLoS Comput Biol"
    assert journal["abstract"] == "Some abstract"
    assert journal["url"] == "https://doi.org/10.1/med"
    assert preprint["peer_reviewed"] is False
    assert preprint["journal"] == "bioRxiv"
    assert preprint["authors"] == ["Zhang Y", "Han D"]
    assert preprint["url"] == "https://europepmc.org/article/PPR/PPR1"
    queries = [kwargs["params"]["query"] for _, kwargs in session.calls]
    assert queries == ["(hodgkin huxley) AND SRC:MED", "(hodgkin huxley) AND SRC:PPR"]


async def test_pubmed_is_the_life_sciences_fallback(monkeypatch):
    monkeypatch.setenv("KLEA_INGEST_MAILTO", "someone@example.org")
    session = _FakeSession(
        {
            EUROPEPMC_SEARCH_URL: _FakeResponse(status=429),
            PUBMED_ESEARCH_URL: _pubmed_search(["222", "111"]),
            PUBMED_EFETCH_URL: _FakeResponse(text=PUBMED_XML),
        }
    )

    result = await search_papers(session, "sodium channels", domain="life-sciences")

    assert session.urls() == [
        EUROPEPMC_SEARCH_URL,
        PUBMED_ESEARCH_URL,
        PUBMED_EFETCH_URL,
    ]
    first, second = result["results"]
    assert first["source"] == "pubmed"
    assert first["peer_reviewed"] is True
    assert first["title"] == "Sodium channels at 60"
    assert first["authors"] == ["William A Catterall"]
    assert first["year"] == 2012
    assert first["journal"] == "The Journal of physiology"
    assert first["abstract"] == "BACKGROUND: Some background. RESULTS: Some results."
    assert first["doi"] == "10.1113/jphysiol.2011.224204"
    assert first["url"] == "https://doi.org/10.1113/jphysiol.2011.224204"
    assert second["title"] == "Second by relevance"
    assert second["authors"] == ["Neuro Consortium"]
    assert second["year"] == 1998
    assert second["doi"] is None
    assert second["url"] == "https://pubmed.ncbi.nlm.nih.gov/111/"

    search_params = session.calls[1][1]["params"]
    assert search_params["term"] == "(sodium channels) NOT preprint[pt]"
    assert search_params["sort"] == "relevance"
    assert search_params["tool"] == "klea"
    assert search_params["email"] == "someone@example.org"
    assert session.calls[2][1]["params"]["id"] == "222,111"


async def test_pubmed_with_no_hits_skips_efetch():
    session = _FakeSession(
        {
            EUROPEPMC_SEARCH_URL: _FakeResponse({"resultList": {"result": []}}),
            PUBMED_ESEARCH_URL: _pubmed_search([]),
            OPENALEX_WORKS_URL: _openalex([_openalex_work()]),
        }
    )

    result = await search_papers(session, "hodgkin huxley", domain="life-sciences")

    assert PUBMED_EFETCH_URL not in session.urls()
    assert [p["source"] for p in result["results"]] == ["openalex"]


async def test_pubmed_search_error_is_a_source_failure():
    session = _FakeSession(
        {
            EUROPEPMC_SEARCH_URL: _FakeResponse(status=500),
            PUBMED_ESEARCH_URL: _FakeResponse(
                {"esearchresult": {"ERROR": "Invalid query"}}
            ),
            OPENALEX_WORKS_URL: _openalex([_openalex_work()]),
        }
    )

    result = await search_papers(session, "hodgkin huxley", domain="life-sciences")

    assert [p["source"] for p in result["results"]] == ["openalex"]
    assert "pubmed: PubMed search error: Invalid query" in result["note"]


async def test_arxiv_parsing_and_query():
    session = _FakeSession(
        {
            OPENALEX_WORKS_URL: _openalex([_openalex_work()]),
            ARXIV_QUERY_URL: _FakeResponse(text=ARXIV_FEED),
        }
    )

    result = await search_papers(session, "hodgkin huxley", preprints=True)

    peer, preprint = result["results"]
    assert peer["source"] == "openalex"
    assert preprint["source"] == "arxiv"
    assert preprint["peer_reviewed"] is False
    assert preprint["title"] == "Structure-preserving numerical integrators"
    assert preprint["authors"] == ["Zhengdao Chen", "Ari Stern"]
    assert preprint["year"] == 2018
    assert preprint["doi"] == "10.1137/18M123390X"
    assert preprint["url"] == "https://arxiv.org/abs/1811.00173v2"
    arxiv_params = session.calls[-1][1]["params"]
    assert arxiv_params["search_query"] == "all:hodgkin AND all:huxley"


async def test_arxiv_invalid_xml_is_a_source_failure():
    session = _FakeSession(
        {
            OPENALEX_WORKS_URL: _openalex([]),
            CROSSREF_WORKS_URL: _crossref([]),
            ARXIV_QUERY_URL: _FakeResponse(text="<not xml"),
        }
    )

    result = await search_papers(session, "hodgkin huxley", preprints=True)

    assert result["results"] == []
    assert "arxiv" in result["note"]


async def test_semantic_scholar_skipped_without_key():
    session = _FakeSession(
        {
            OPENALEX_WORKS_URL: _FakeResponse(status=429),
            CROSSREF_WORKS_URL: _crossref([]),
        }
    )

    await search_papers(session, "hodgkin huxley")

    assert SEMANTIC_SCHOLAR_SEARCH_URL not in session.urls()


async def test_semantic_scholar_used_with_key(monkeypatch):
    monkeypatch.setenv("SEMANTIC_SCHOLAR_API_KEY", "test-key")
    paper = {
        "title": "S2 paper",
        "authors": [{"name": "Andrew Huxley"}],
        "year": 1952,
        "venue": "J. Physiol.",
        "abstract": "An abstract",
        "externalIds": {"DOI": "10.1/s2"},
        "url": "https://www.semanticscholar.org/paper/abc",
    }
    session = _FakeSession(
        {
            OPENALEX_WORKS_URL: _FakeResponse(status=429),
            CROSSREF_WORKS_URL: _FakeResponse(status=429),
            SEMANTIC_SCHOLAR_SEARCH_URL: _FakeResponse({"data": [paper]}),
        }
    )

    result = await search_papers(session, "hodgkin huxley")

    [record] = result["results"]
    assert record["source"] == "semantic_scholar"
    assert record["doi"] == "10.1/s2"
    url, kwargs = session.calls[-1]
    assert url == SEMANTIC_SCHOLAR_SEARCH_URL
    assert kwargs["headers"]["x-api-key"] == "test-key"
    assert kwargs["params"]["publicationTypes"] == "JournalArticle"


async def test_semantic_scholar_drops_preprints_tagged_as_journal_articles(
    monkeypatch,
):
    monkeypatch.setenv("SEMANTIC_SCHOLAR_API_KEY", "test-key")

    def _paper(title, doi=None, venue="Sci. Rep."):
        return {
            "title": title,
            "venue": venue,
            "abstract": "An abstract",
            "externalIds": {"DOI": doi} if doi else {},
        }

    papers = [
        _paper(
            "Published, preprint DOI",
            doi="10.48550/arXiv.2406.02173",
            venue="Computer Methods in Applied Mechanics and Engineering",
        ),
        _paper("bioRxiv venue", doi="10.1101/2023.04.17.537224", venue="bioRxiv"),
        _paper("arXiv venue, no DOI", venue="arXiv.org"),
        _paper("Genes Dev paper", doi="10.1101/gad.1234.5", venue="Genes Dev"),
        _paper("Journal paper", doi="10.1038/s41598-024-70655-5"),
    ]
    session = _FakeSession(
        {
            OPENALEX_WORKS_URL: _FakeResponse(status=429),
            CROSSREF_WORKS_URL: _FakeResponse(status=429),
            SEMANTIC_SCHOLAR_SEARCH_URL: _FakeResponse({"data": papers}),
        }
    )

    result = await search_papers(session, "hodgkin huxley", max_results=5)

    assert [p["title"] for p in result["results"]] == [
        "Published, preprint DOI",
        "Genes Dev paper",
        "Journal paper",
    ]
    assert session.calls[-1][1]["params"]["limit"] == 10


async def test_falls_back_when_primary_is_rate_limited():
    session = _FakeSession(
        {
            OPENALEX_WORKS_URL: _FakeResponse(status=429),
            CROSSREF_WORKS_URL: _crossref([_crossref_item()]),
        }
    )

    result = await search_papers(session, "hodgkin huxley")

    assert result["error"] == ""
    assert [p["source"] for p in result["results"]] == ["crossref"]
    assert "openalex" in result["note"]
    assert "429" in result["note"]


async def test_falls_back_on_connection_error():
    session = _FakeSession(
        {
            OPENALEX_WORKS_URL: httpx.ConnectError("boom"),
            CROSSREF_WORKS_URL: _crossref([_crossref_item()]),
        }
    )

    result = await search_papers(session, "hodgkin huxley")

    assert [p["source"] for p in result["results"]] == ["crossref"]


async def test_results_without_abstracts_fall_back_to_next_source(monkeypatch):
    monkeypatch.setattr(search, "_ROTATION", itertools.count(1))
    session = _FakeSession(
        {
            CROSSREF_WORKS_URL: _crossref([_crossref_item(abstract=None)]),
            OPENALEX_WORKS_URL: _openalex([_openalex_work()]),
        }
    )

    result = await search_papers(session, "hodgkin huxley")

    assert session.urls() == [CROSSREF_WORKS_URL, OPENALEX_WORKS_URL]
    assert [p["source"] for p in result["results"]] == ["openalex"]


async def test_results_without_abstracts_kept_as_last_resort(monkeypatch):
    monkeypatch.setattr(search, "_ROTATION", itertools.count(1))
    session = _FakeSession(
        {
            CROSSREF_WORKS_URL: _crossref([_crossref_item(abstract=None)]),
            OPENALEX_WORKS_URL: _FakeResponse(status=429),
        }
    )

    result = await search_papers(session, "hodgkin huxley")

    assert [p["source"] for p in result["results"]] == ["crossref"]
    assert result["error"] == ""


async def test_primary_source_rotates_between_calls():
    session = _FakeSession(
        {
            OPENALEX_WORKS_URL: _openalex([_openalex_work()]),
            CROSSREF_WORKS_URL: _crossref([_crossref_item()]),
        }
    )

    first = await search_papers(session, "hodgkin huxley")
    second = await search_papers(session, "hodgkin huxley")

    assert first["results"][0]["source"] == "openalex"
    assert second["results"][0]["source"] == "crossref"


async def test_error_when_every_source_fails():
    session = _FakeSession(
        {
            OPENALEX_WORKS_URL: _FakeResponse(status=429),
            CROSSREF_WORKS_URL: _FakeResponse(status=500),
        }
    )

    result = await search_papers(session, "hodgkin huxley")

    assert result["results"] == []
    assert result["error"].startswith("No paper source could be reached")


async def test_no_results_is_not_an_error():
    session = _FakeSession(
        {OPENALEX_WORKS_URL: _openalex([]), CROSSREF_WORKS_URL: _crossref([])}
    )

    result = await search_papers(session, "xyzzy nonsense")

    assert result["error"] == ""
    assert result["note"] == "No papers found for this query."


async def test_preprint_duplicates_of_peer_reviewed_papers_are_dropped():
    session = _FakeSession(
        {
            OPENALEX_WORKS_URL: _openalex(
                [_openalex_work(doi="https://doi.org/10.1137/18M123390X")]
            ),
            ARXIV_QUERY_URL: _FakeResponse(text=ARXIV_FEED),
        }
    )

    result = await search_papers(session, "hodgkin huxley", preprints=True)

    assert [p["source"] for p in result["results"]] == ["openalex"]


async def test_long_abstracts_are_truncated():
    work = _openalex_work()
    work["abstract_inverted_index"] = {"word": list(range(MAX_ABSTRACT_CHARS))}
    session = _FakeSession({OPENALEX_WORKS_URL: _openalex([work])})

    result = await search_papers(session, "hodgkin huxley")

    abstract = result["results"][0]["abstract"]
    assert len(abstract) <= MAX_ABSTRACT_CHARS + len("...")
    assert abstract.endswith("...")


async def test_max_results_caps_each_group():
    works = [_openalex_work(title=f"Paper {i}", doi=f"10.1/{i}") for i in range(5)]
    session = _FakeSession({OPENALEX_WORKS_URL: _openalex(works)})

    result = await search_papers(session, "hodgkin huxley", max_results=2)

    assert len(result["results"]) == 2
    assert session.calls[0][1]["params"]["per-page"] == 2


@pytest.mark.parametrize(
    ("query", "domain", "message"),
    [
        ("   ", "general", "Query is empty"),
        ("hodgkin", "physics", "Unknown domain"),
    ],
)
async def test_invalid_input(query, domain, message):
    result = await search_papers(_FakeSession({}), query, domain=domain)

    assert result["error"].startswith(message)
    assert result["results"] == []


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (
            "https://doi.org/10.1088/0957-4484/24/38/383001",
            "10.1088/0957-4484/24/38/383001",
        ),
        ("http://dx.doi.org/10.1/abc", "10.1/abc"),
        ("doi: 10.1/abc", "10.1/abc"),
        ("10.1/abc", "10.1/abc"),
        ("", None),
        (None, None),
    ],
)
def test_clean_doi(raw, expected):
    assert clean_doi(raw) == expected


@pytest.mark.parametrize(
    ("doi", "journal", "expected"),
    [
        ("10.1101/2023.04.17.537224", "bioRxiv", True),
        (None, "medRxiv", True),
        (None, "arXiv.org", True),
        ("10.21203/rs.3.rs-123/v1", "Research Square", True),
        (None, "Preprints.org", True),
        ("10.2139/ssrn.3193693", "SSRN Electronic Journal", True),
        (
            "10.48550/arXiv.2406.02173",
            "Computer Methods in Applied Mechanics and Engineering",
            False,
        ),
        ("10.48550/arXiv.2406.02173", None, False),
        ("10.1101/gad.1234.5", "Genes Dev", False),
        ("10.1038/81426", "Nature Neuroscience", False),
        (None, None, False),
    ],
)
def test_is_preprint(doi, journal, expected):
    assert is_preprint(PaperRecord(title="t", doi=doi, journal=journal)) is expected


async def test_throttle_keeps_minimum_gap():
    throttle = Throttle(min_interval=0.1)
    started = time.monotonic()
    async with throttle:
        pass
    async with throttle:
        pass
    assert time.monotonic() - started >= 0.1


async def test_bundled_wrapper_uses_lifespan_session():
    from klea_utils.mcp.server import bundled_tools

    session = _FakeSession({OPENALEX_WORKS_URL: _openalex([_openalex_work()])})
    ctx = SimpleNamespace(lifespan_context={"http_session": session})

    result = await bundled_tools.search_papers(ctx=ctx, query="hodgkin huxley")

    assert result.is_error is False
    assert result.structured_content["results"][0]["source"] == "openalex"


async def test_bundled_wrapper_reports_failure_as_error():
    from klea_utils.mcp.server import bundled_tools

    ctx = SimpleNamespace(lifespan_context={})

    result = await bundled_tools.search_papers(ctx=ctx, query="hodgkin huxley")

    assert result.is_error is True
    assert "HTTP session not initialized" in result.structured_content["error"]
