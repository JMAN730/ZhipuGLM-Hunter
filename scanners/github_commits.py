"""GitHub commit-search scanner.

Scans commit messages and, for each hit, the commit's file patches — the classic
"committed .env then deleted it" leak that code search no longer surfaces. Every
hit is repo-bound, so findings are disclosable via an issue on the same repo.
"""

from __future__ import annotations

import asyncio
import urllib.parse

import aiohttp

from .base import BaseScanner, auto_github_token, github_api_headers


class GitHubCommitsScanner(BaseScanner):
    BASE = "https://api.github.com"

    def __init__(self, token: str = "", pages: int = 2, per_page: int = 30, **kwargs):
        super().__init__(**kwargs)
        self.token = token or auto_github_token()
        self.pages = pages
        self.per_page = per_page
        self._headers = github_api_headers(self.token)

    @property
    def source_name(self) -> str:
        return "github_commits"

    def _keys_from_commit(self, item: dict, detail: dict | None = None) -> list[dict]:
        parts = [(item.get("commit") or {}).get("message") or ""]
        first_file = ""
        if detail:
            for changed in detail.get("files", []) or []:
                parts.append(changed.get("patch", "") or "")
                if not first_file:
                    first_file = changed.get("filename", "") or ""
        text = "\n".join(parts)
        repo = (item.get("repository") or {}).get("full_name", "")
        url = item.get("html_url", "")
        return [
            {"key": key, "source": self.source_name, "repo": repo, "file": first_file, "url": url}
            for key in self.extract_local(text)
        ]

    async def search(self, query: str | None = None) -> list[dict]:
        self.results = []
        if not query:
            return self.results

        sem = asyncio.Semaphore(self.concurrency)
        async with aiohttp.ClientSession(headers=self._headers) as session:
            for page in range(1, self.pages + 1):
                if self._should_stop():
                    break
                items = await self._search_page(session, query, page)
                if not items:
                    break
                await asyncio.gather(*(self._scan_commit(session, sem, item) for item in items))
                if len(items) < self.per_page:
                    break
        return self.results

    async def _search_page(self, session: aiohttp.ClientSession, query: str, page: int) -> list[dict]:
        params = urllib.parse.urlencode({"q": query, "per_page": self.per_page, "page": page})
        url = f"{self.BASE}/search/commits?{params}"
        for attempt in range(3):
            try:
                async with session.get(url, timeout=aiohttp.ClientTimeout(total=self.timeout)) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        return data.get("items", [])
                    if resp.status in {403, 429}:
                        await asyncio.sleep(5 * (attempt + 1))
                        continue
                    return []
            except (asyncio.TimeoutError, aiohttp.ClientError):
                await asyncio.sleep(1 + attempt)
        return []

    async def _scan_commit(self, session: aiohttp.ClientSession, sem: asyncio.Semaphore, item: dict):
        async with sem:
            detail = await self._fetch_detail(session, item)
            self.results.extend(self._keys_from_commit(item, detail))

    async def _fetch_detail(self, session: aiohttp.ClientSession, item: dict) -> dict | None:
        repo = (item.get("repository") or {}).get("full_name", "")
        sha = item.get("sha", "")
        if not repo or not sha:
            return None
        url = f"{self.BASE}/repos/{repo}/commits/{sha}"
        try:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=self.timeout)) as resp:
                if resp.status == 200:
                    return await resp.json()
        except (asyncio.TimeoutError, aiohttp.ClientError):
            return None
        return None
