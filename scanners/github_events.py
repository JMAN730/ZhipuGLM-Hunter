"""GitHub Events monitor — scans recent public PushEvents for exposed keys."""

from __future__ import annotations

import asyncio
from collections import deque

import aiohttp

from .base import BaseScanner, auto_github_token, github_api_headers


class GitHubEventsScanner(BaseScanner):
    BASE = "https://api.github.com"
    POLL_INTERVAL = 60

    def __init__(
        self,
        token: str = "",
        poll_interval: int = 60,
        max_events_per_poll: int = 30,
        max_polls: int = 1,
        **kwargs,
    ):
        super().__init__(**kwargs)
        self.token = token or auto_github_token()
        self.poll_interval = poll_interval or self.POLL_INTERVAL
        self.max_events_per_poll = max_events_per_poll
        self.max_polls = max(1, max_polls)
        self._headers = github_api_headers(self.token)
        self._seen_commits: set[str] = set()
        self._event_queue: deque = deque(maxlen=500)

    @property
    def source_name(self) -> str:
        return "github_events"

    async def search(self, query: str | None = None) -> list[dict]:
        self.results = []
        sem = asyncio.Semaphore(self.concurrency)

        async with aiohttp.ClientSession(headers=self._headers) as session:
            for _ in range(self.max_polls):
                if self._should_stop():
                    break
                events = await self._fetch_events(session)
                push_events = [
                    event for event in events if event.get("type") == "PushEvent" and event.get("public")
                ]
                for event in push_events[: self.max_events_per_poll]:
                    if self._should_stop():
                        break
                    await self._handle_push(session, sem, event)
                if self.max_polls > 1 and not self._should_stop():
                    await asyncio.sleep(self.poll_interval)
        return self.results

    async def _fetch_events(self, session: aiohttp.ClientSession) -> list[dict]:
        url = f"{self.BASE}/events?per_page=30"
        try:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=self.timeout)) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    return data if isinstance(data, list) else []
                if resp.status == 403:
                    await asyncio.sleep(min(self.poll_interval, 300))
        except (asyncio.TimeoutError, aiohttp.ClientError):
            pass
        return []

    async def _handle_push(self, session: aiohttp.ClientSession, sem: asyncio.Semaphore, event: dict):
        repo = event.get("repo", {}).get("name", "")
        commits = (event.get("payload") or {}).get("commits") or []

        for commit in commits:
            sha = commit.get("sha", "")
            if not sha or sha in self._seen_commits:
                continue
            self._seen_commits.add(sha)

            message = commit.get("message", "") or ""
            for key in self.extract_local(message):
                self.results.append(
                    {
                        "key": key,
                        "source": self.source_name,
                        "repo": repo,
                        "file": sha[:7],
                        "url": f"https://github.com/{repo}/commit/{sha}" if repo else "",
                    }
                )

            changed = (commit.get("added") or []) + (commit.get("modified") or [])
            for path in changed[:20]:
                if self._should_stop():
                    return
                raw_url = f"https://raw.githubusercontent.com/{repo}/{sha}/{path}" if repo else ""
                if not raw_url:
                    continue
                async with sem:
                    try:
                        async with session.get(raw_url, timeout=aiohttp.ClientTimeout(total=self.timeout)) as resp:
                            if resp.status != 200:
                                continue
                            text = await resp.text()
                            for key in self.extract_local(text):
                                self.results.append(
                                    {
                                        "key": key,
                                        "source": self.source_name,
                                        "repo": repo,
                                        "file": path,
                                        "url": f"https://github.com/{repo}/blob/{sha}/{path}" if repo else raw_url,
                                    }
                                )
                    except (asyncio.TimeoutError, aiohttp.ClientError):
                        continue
