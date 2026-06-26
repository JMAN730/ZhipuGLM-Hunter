"""Shared scanner helpers."""

from __future__ import annotations

import asyncio
import hashlib
import os
import re
import subprocess
import time
from abc import ABC, abstractmethod

import aiohttp

KEY_PATTERN = re.compile(r"\b[a-f0-9]{32}\.[A-Za-z0-9]{8,64}\b")

GITHUB_USER_AGENT = "ZhipuGLMHunter/0.1"


def auto_github_token(_runner=subprocess.run) -> str:
    """Best-effort GitHub token: env vars first, then ``gh auth token``.

    Returns an empty string if none is available. ``_runner`` is injectable so
    the lookup can be exercised offline in tests.
    """
    for env_var in ("GH_TOKEN", "GITHUB_TOKEN"):
        token = os.environ.get(env_var, "")
        if token:
            return token
    try:
        result = _runner(
            ["gh", "auth", "token"],
            capture_output=True,
            timeout=5,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
        if result is not None and getattr(result, "returncode", 1) == 0 and result.stdout.strip():
            return result.stdout.strip()
    except Exception:
        pass
    return ""


def github_api_headers(token: str, accept: str = "application/vnd.github+json") -> dict:
    """Standard GitHub REST headers, with bearer auth only when a token exists."""
    headers = {"Accept": accept, "User-Agent": GITHUB_USER_AGENT}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


class RateLimiter:
    """Header-aware GitHub rate-limit gate. Optionally persists the block
    window via a StateStore so a restarted run honors an in-flight limit."""

    def __init__(self, resource: str = "github_search", store=None, max_wait: float = 900.0):
        self.resource = resource
        self._store = store
        self._max_wait = max_wait
        self._blocked_until = 0.0
        if store is not None:
            persisted = store.get_block_until(resource)
            if persisted:
                self._blocked_until = persisted

    @staticmethod
    def compute_block_until(status: int, headers, now: float) -> float:
        retry_after = headers.get("Retry-After")
        if retry_after is not None:
            try:
                return now + float(retry_after)
            except ValueError:
                pass
        if headers.get("X-RateLimit-Remaining") == "0" and headers.get("X-RateLimit-Reset"):
            try:
                return float(headers["X-RateLimit-Reset"])
            except ValueError:
                pass
        if status in (403, 429):
            return now + 60.0
        return 0.0

    def note_response(self, status: int, headers) -> None:
        block = self.compute_block_until(status, headers, time.time())
        if block > self._blocked_until:
            self._blocked_until = block
            if self._store is not None:
                self._store.set_block_until(self.resource, block)

    async def wait_if_blocked(self) -> None:
        delay = self._blocked_until - time.time()
        if delay > 0:
            await asyncio.sleep(min(delay, self._max_wait))


BAD_PATTERNS = [
    "your",
    "xxx",
    "example",
    "placeholder",
    "replace",
    "demo",
    "sample",
    "fake",
    "dummy",
    "changeme",
    "insert",
]


def _low_entropy(value: str) -> bool:
    return len(set(value)) < 4 or value.isdigit()


def is_bad_key(key: str, extra_bad: list[str] | None = None) -> bool:
    lower = key.lower()
    patterns = BAD_PATTERNS + (extra_bad or [])
    if any(pattern.lower() in lower for pattern in patterns):
        return True

    if not KEY_PATTERN.fullmatch(key):
        return True

    key_id, secret = key.split(".", 1)
    return _low_entropy(key_id) or _low_entropy(secret)


def redact_key(key: str) -> str:
    if "." not in key or len(key) < 16:
        return key[:6] + "..."
    key_id, secret = key.split(".", 1)
    return f"{key_id[:8]}...{key_id[-4:]}.{secret[:4]}...{secret[-4:]}"


def extract_keys(text: str, extra_bad: list[str] | None = None) -> list[str]:
    keys = KEY_PATTERN.findall(text or "")
    return [key for key in keys if not is_bad_key(key, extra_bad)]


def finding_digest(result: dict) -> str:
    raw = f"{result.get('source', '')}:{result.get('key', '')}:{result.get('url', '')}"
    return hashlib.md5(raw.encode(), usedforsecurity=False).hexdigest()


def dedup_results(results: list[dict]) -> list[dict]:
    seen = set()
    out = []
    for result in results:
        digest = finding_digest(result)
        if digest not in seen:
            seen.add(digest)
            out.append(result)
    return out


class BaseScanner(ABC):
    def __init__(
        self,
        concurrency: int = 10,
        timeout: int = 15,
        extra_bad_patterns: list[str] | None = None,
        session=None,
        rate_limiter: "RateLimiter | None" = None,
    ):
        self.concurrency = concurrency
        self.timeout = timeout
        self.extra_bad = extra_bad_patterns or []
        self._session = session
        self._rate_limiter = rate_limiter
        self._stop_requested = False
        self.results: list[dict] = []

    @abstractmethod
    async def search(self, query: str | None = None) -> list[dict]: ...

    @property
    @abstractmethod
    def source_name(self) -> str: ...

    def extract_local(self, text: str) -> list[str]:
        return extract_keys(text, self.extra_bad)

    def stop(self):
        self._stop_requested = True

    def _should_stop(self) -> bool:
        return self._stop_requested

    def _rate_limit_wait(self, delay: float = 1.0):
        time.sleep(delay)

    def _add_result(self, key: str, url: str, repo: str = "", file_path: str = "", source: str = ""):
        self.results.append(
            {
                "key": key,
                "source": source or self.source_name,
                "repo": repo,
                "file": file_path,
                "url": url,
            }
        )

    async def _rl_get_items(self, session, url: str) -> list[dict]:
        """Rate-limit-aware GET of a GitHub /search endpoint; returns items[]."""
        for attempt in range(3):
            if self._rate_limiter is not None:
                await self._rate_limiter.wait_if_blocked()
            try:
                async with session.get(url, timeout=aiohttp.ClientTimeout(total=self.timeout)) as resp:
                    if self._rate_limiter is not None:
                        self._rate_limiter.note_response(resp.status, resp.headers)
                    if resp.status == 200:
                        data = await resp.json()
                        return data.get("items", [])
                    if resp.status in {403, 429}:
                        if self._rate_limiter is not None:
                            await self._rate_limiter.wait_if_blocked()
                        else:
                            await asyncio.sleep(5 * (attempt + 1))
                        continue
                    return []
            except (asyncio.TimeoutError, aiohttp.ClientError):
                await asyncio.sleep(1 + attempt)
        return []
