"""Shared scanner helpers."""

from __future__ import annotations

import hashlib
import os
import re
import subprocess
import time
from abc import ABC, abstractmethod

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
    ):
        self.concurrency = concurrency
        self.timeout = timeout
        self.extra_bad = extra_bad_patterns or []
        self._session = session
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
