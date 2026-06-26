"""GitHub Issues / Pull-Request search scanner.

Keys frequently get pasted into issue or PR bodies (stack traces, "here's my
config", reproduction steps). Every hit is repo-bound, so a finding here can be
disclosed by opening an issue on the same repo.
"""

from __future__ import annotations

import urllib.parse

import aiohttp

from .base import BaseScanner, auto_github_token, github_api_headers


class GitHubIssuesScanner(BaseScanner):
    BASE = "https://api.github.com"

    def __init__(self, token: str = "", pages: int = 2, per_page: int = 30, **kwargs):
        super().__init__(**kwargs)
        self.token = token or auto_github_token()
        self.pages = pages
        self.per_page = per_page
        self._headers = github_api_headers(self.token)

    @property
    def source_name(self) -> str:
        return "github_issues"

    @staticmethod
    def _repo_from_api_url(repository_url: str) -> str:
        """``https://api.github.com/repos/owner/name`` -> ``owner/name``."""
        marker = "/repos/"
        idx = (repository_url or "").find(marker)
        return repository_url[idx + len(marker) :] if idx != -1 else ""

    def _keys_from_issue_item(self, item: dict) -> list[dict]:
        text = f"{item.get('title', '')}\n{item.get('body', '') or ''}"
        repo = self._repo_from_api_url(item.get("repository_url", ""))
        url = item.get("html_url", "")
        return [
            {"key": key, "source": self.source_name, "repo": repo, "file": "", "url": url}
            for key in self.extract_local(text)
        ]

    async def search(self, query: str | None = None) -> list[dict]:
        self.results = []
        if not query:
            return self.results

        async with aiohttp.ClientSession(headers=self._headers) as session:
            for page in range(1, self.pages + 1):
                if self._should_stop():
                    break
                items = await self._search_page(session, query, page)
                if not items:
                    break
                for item in items:
                    self.results.extend(self._keys_from_issue_item(item))
                if len(items) < self.per_page:
                    break
        return self.results

    async def _search_page(self, session: aiohttp.ClientSession, query: str, page: int) -> list[dict]:
        params = urllib.parse.urlencode({"q": query, "per_page": self.per_page, "page": page})
        url = f"{self.BASE}/search/issues?{params}"
        return await self._rl_get_items(session, url)
