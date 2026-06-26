"""GitHub Code Search scanner."""

from __future__ import annotations

import asyncio
import subprocess
import urllib.parse

import aiohttp

from .base import BaseScanner, extract_keys


def _auto_token() -> str:
    try:
        result = subprocess.run(
            ["gh", "auth", "token"],
            capture_output=True,
            timeout=5,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
        return result.stdout.strip() if result.returncode == 0 else ""
    except Exception:
        return ""


class GitHubCodeScanner(BaseScanner):
    BASE = "https://api.github.com"

    def __init__(self, token: str = "", pages: int = 2, per_page: int = 30, **kwargs):
        super().__init__(**kwargs)
        self.token = token or _auto_token()
        self.pages = pages
        self.per_page = per_page
        self._headers = {
            "Accept": "application/vnd.github+json",
            "User-Agent": "ZhipuGLMHunter/0.1",
        }
        if self.token:
            self._headers["Authorization"] = f"Bearer {self.token}"

    @property
    def source_name(self) -> str:
        return "github_code"

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

                await asyncio.gather(*(self._scan_item(session, sem, item) for item in items))

                if len(items) < self.per_page:
                    break

        return self.results

    async def _search_page(self, session: aiohttp.ClientSession, query: str, page: int) -> list[dict]:
        params = urllib.parse.urlencode({"q": query, "per_page": self.per_page, "page": page})
        url = f"{self.BASE}/search/code?{params}"
        return await self._rl_get_items(session, url)

    async def _scan_item(self, session: aiohttp.ClientSession, sem: asyncio.Semaphore, item: dict):
        repo_info = item.get("repository") or {}
        repo = repo_info.get("full_name", "")
        branch = repo_info.get("default_branch", "main")
        path = item.get("path", "")
        html_url = item.get("html_url", "")

        raw_url = self._raw_url(repo, branch, path)
        if not raw_url:
            return

        async with sem:
            for attempt in range(2):
                try:
                    async with session.get(raw_url, timeout=aiohttp.ClientTimeout(total=self.timeout)) as resp:
                        if resp.status == 200:
                            text = await resp.text(errors="replace")
                            for key in extract_keys(text, self.extra_bad):
                                self._add_result(key, html_url, repo, path, self.source_name)
                        return
                except (asyncio.TimeoutError, aiohttp.ClientError):
                    await asyncio.sleep(1 + attempt)

    @staticmethod
    def _raw_url(repo: str, branch: str, path: str) -> str:
        if not repo or not branch or not path:
            return ""
        encoded_path = "/".join(urllib.parse.quote(part) for part in path.split("/"))
        encoded_branch = urllib.parse.quote(branch, safe="")
        return f"https://raw.githubusercontent.com/{repo}/{encoded_branch}/{encoded_path}"
