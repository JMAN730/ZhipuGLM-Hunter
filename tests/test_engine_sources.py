"""Network-free tests for the engine's multi-source search wiring."""

import asyncio

from scanner_engine import DEFAULT_SOURCES, KEYWORD_QUERIES, ScannerEngine
from scanners.github_code import GitHubCodeScanner
from scanners.github_commits import GitHubCommitsScanner
from scanners.github_issues import GitHubIssuesScanner


def test_default_sources_are_all_github_repo_bound():
    assert ScannerEngine().sources == list(DEFAULT_SOURCES)
    assert ScannerEngine(sources=["github_code"]).sources == ["github_code"]


def test_queries_for_source_picks_code_vs_keyword():
    engine = ScannerEngine()
    code_queries = ["zhipu filename:env"]
    assert engine._queries_for_source("github_code", code_queries) == code_queries
    assert engine._queries_for_source("github_commits", code_queries) == KEYWORD_QUERIES
    assert engine._queries_for_source("github_issues", code_queries) == KEYWORD_QUERIES


def test_build_scanner_returns_expected_types():
    engine = ScannerEngine()
    assert isinstance(engine._build_scanner("github_code"), GitHubCodeScanner)
    assert isinstance(engine._build_scanner("github_commits"), GitHubCommitsScanner)
    assert isinstance(engine._build_scanner("github_issues"), GitHubIssuesScanner)


class _FakeScanner:
    def __init__(self, rows):
        self._rows = rows

    async def search(self, query):
        return list(self._rows)


def test_search_all_aggregates_across_sources_and_dedups(monkeypatch):
    engine = ScannerEngine(search_delay=0, sources=["github_code", "github_issues"])
    rows = {
        "github_code": [{"source": "github_code", "key": "K", "url": "u1", "repo": "a/b"}],
        # same key, different source+url -> a distinct location, not a dup
        "github_issues": [{"source": "github_issues", "key": "K", "url": "u2", "repo": "a/b"}],
    }
    monkeypatch.setattr(engine, "_build_scanner", lambda source: _FakeScanner(rows[source]))
    monkeypatch.setattr(engine, "_queries_for_source", lambda source, code_queries: ["q"])

    discovered = asyncio.run(engine._search_all(["codeq"]))
    assert len(discovered) == 2
    assert {row["source"] for row in discovered} == {"github_code", "github_issues"}


def test_search_all_dedups_identical_locations(monkeypatch):
    engine = ScannerEngine(search_delay=0, sources=["github_code"])
    row = [{"source": "github_code", "key": "K", "url": "u1", "repo": "a/b"}]
    monkeypatch.setattr(engine, "_build_scanner", lambda source: _FakeScanner(row))
    # two identical queries -> same location twice -> dedup to one
    monkeypatch.setattr(engine, "_queries_for_source", lambda source, code_queries: ["q", "q"])

    discovered = asyncio.run(engine._search_all(["codeq"]))
    assert len(discovered) == 1
