"""GitHub Gist scanner — scans recent public gists for exposed keys."""

from __future__ import annotations

import asyncio

import aiohttp

from .base import BaseScanner, auto_github_token, github_api_headers


class GitHubGistScanner(BaseScanner):
    BASE = "https://api.github.com"

    def __init__(self, token: str = "", pages: int = 2, per_page: int = 100, **kwargs):
        super().__init__(**kwargs)
        self.token = token or auto_github_token()
        self.pages = pages
        self.per_page = per_page
        self._headers = github_api_headers(self.token)

    @property
    def source_name(self) -> str:
        return "github_gist"

    async def search(self, query: str | None = None) -> list[dict]:
        self.results = []
        sem = asyncio.Semaphore(self.concurrency)

        async with aiohttp.ClientSession(headers=self._headers) as session:
            for page in range(1, self.pages + 1):
                if self._should_stop():
                    break
                gists = await self._fetch_page(session, page)
                if not gists:
                    break
                await asyncio.gather(*(self._scan_gist(session, sem, gist) for gist in gists))
                if len(gists) < self.per_page:
                    break
        return self.results

    async def _fetch_page(self, session: aiohttp.ClientSession, page: int) -> list[dict]:
        url = f"{self.BASE}/gists/public?per_page={self.per_page}&page={page}"
        for attempt in range(3):
            try:
                async with session.get(url, timeout=aiohttp.ClientTimeout(total=self.timeout)) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        return data if isinstance(data, list) else []
                    if resp.status in {403, 429}:
                        await asyncio.sleep(5 * (attempt + 1))
                        continue
                    return []
            except (asyncio.TimeoutError, aiohttp.ClientError):
                await asyncio.sleep(1 + attempt)
        return []

    async def _scan_gist(self, session: aiohttp.ClientSession, sem: asyncio.Semaphore, gist: dict):
        gist_id = gist.get("id", "")
        owner = (gist.get("owner") or {}).get("login", "")
        html_url = gist.get("html_url", "")
        repo = f"{owner}/gist" if owner else "gist/unknown"
        description = gist.get("description", "") or ""

        for key in self.extract_local(description):
            self.results.append(
                {
                    "key": key,
                    "source": self.source_name,
                    "repo": repo,
                    "file": "description",
                    "url": html_url,
                }
            )

        for fname, finfo in (gist.get("files") or {}).items():
            raw_url = finfo.get("raw_url", "")
            if not raw_url:
                continue
            async with sem:
                for attempt in range(2):
                    try:
                        async with session.get(raw_url, timeout=aiohttp.ClientTimeout(total=self.timeout)) as resp:
                            if resp.status == 200:
                                text = await resp.text()
                                for key in self.extract_local(text):
                                    self.results.append(
                                        {
                                            "key": key,
                                            "source": self.source_name,
                                            "repo": repo,
                                            "file": fname,
                                            "url": html_url,
                                        }
                                    )
                            break
                    except (asyncio.TimeoutError, aiohttp.ClientError):
                        await asyncio.sleep(1)
                    except Exception:
                        break
