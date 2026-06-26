"""Core engine for ZhipuGLM Hunter."""

from __future__ import annotations

import asyncio
import csv
import hashlib
import json
import os
import subprocess
import time
from datetime import datetime
from pathlib import Path
from typing import Callable

import aiohttp

from scanners.base import RateLimiter, dedup_results, extract_keys, is_bad_key, redact_key
from scanners.github_code import GitHubCodeScanner
from scanners.github_commits import GitHubCommitsScanner
from scanners.github_issues import GitHubIssuesScanner
from state_store import DEAD, ERROR, LIVE, NOBALANCE, StateStore

ZHIPU_API_BASE = "https://open.bigmodel.cn/api/paas/v4"
VERIFY_PATH = "/models"
BALANCE_PATH = "/user/balance"
RESULT_BASENAME = "zhipu_keys_result"
DEFAULT_USD_CNY_RATE = 7.25

PROVIDER_CONFIG = {
    "name": "zhipu",
    "display": "Zhipu AI",
    "base": ZHIPU_API_BASE,
    "verify_url": VERIFY_PATH,
    "balance_url": BALANCE_PATH,
}

BUILTIN_QUERIES = [
    "zhipu api key filename:env",
    "ZHIPU_API_KEY filename:env",
    "ZHIPUAI_API_KEY filename:env",
    "GLM_API_KEY filename:env",
    "BIGMODEL_API_KEY filename:env",
    "open.bigmodel.cn filename:env",
    "zhipu filename:credentials",
    "zhipu filename:secrets",
    "zhipu filename:yml",
    "zhipu filename:yaml",
    "zhipu filename:json",
    "zhipu filename:toml",
    "zhipu api key filename:py",
    "ZhipuAI api_key filename:py",
    "zhipuai filename:py NOT env",
    "open.bigmodel.cn filename:py",
    "chatglm api key filename:py",
    "glm-4 api key filename:py",
    "zhipu api key filename:js",
    "zhipu api key filename:ts",
    "open.bigmodel.cn filename:js",
    "open.bigmodel.cn filename:ts",
    "langchain zhipu api_key",
    "dify zhipu api_key",
    "litellm zhipu api_key",
    "autogen zhipu api_key",
    "智谱 api key",
    "bigmodel api key",
]

# GitHub sources scanned by default. All three are repo-bound, so any live key
# they surface can be responsibly disclosed via an issue on the affected repo.
DEFAULT_SOURCES = ("github_code", "github_commits", "github_issues")

# Plain free-text queries for the commit/issue search APIs, which do NOT support
# the code-search `filename:` qualifier that fills queries_v4.txt.
KEYWORD_QUERIES = [
    "zhipu api key",
    "ZHIPU_API_KEY",
    "ZHIPUAI_API_KEY",
    "GLM_API_KEY",
    "BIGMODEL_API_KEY",
    "open.bigmodel.cn",
    "bigmodel api key",
    "zhipuai api key",
    "chatglm api key",
    "glm-4 api key",
    "智谱 api key",
    "智谱 密钥",
]


def parse_zhipu_models_response(data: dict) -> dict:
    """Treat a model-list shaped response as evidence that the key authenticates."""
    if isinstance(data.get("data"), list):
        return {
            "valid": True,
            "provider": "zhipu",
            "total_balance": 0.0,
            "balance_details": [],
            "primary_currency": "CNY",
            "balance_unavailable": True,
            "provider_note": "Valid key (balance not available via API)",
        }
    return {"valid": False, "provider": "zhipu", "reason": "unexpected_response"}


def liveness_status(result: dict) -> str:
    """Map a verify result to a cached liveness status (see state_store)."""
    if result.get("valid"):
        return LIVE
    reason = result.get("reason", "")
    if reason == "invalid_key":
        return DEAD
    if reason == "insufficient_balance":
        return NOBALANCE
    return ERROR


def parse_zhipu_balance(data: dict) -> dict:
    """Parse Zhipu /user/balance response."""
    if "balance_infos" not in data:
        return {"valid": False, "provider": "zhipu", "reason": "unexpected_response"}

    balance_infos = data.get("balance_infos", [])
    total = 0.0
    details = []
    primary_currency = "CNY"
    for info in balance_infos:
        currency = info.get("currency", "CNY")
        total_balance = float(info.get("total_balance", 0))
        granted_balance = float(info.get("granted_balance", 0))
        tipped_balance = float(info.get("tipped_balance", 0))
        total += total_balance
        details.append(
            {
                "currency": currency,
                "total_balance": total_balance,
                "granted_balance": granted_balance,
                "tipped_balance": tipped_balance,
            }
        )
        if currency == "USD":
            primary_currency = "USD"

    return {
        "valid": True,
        "provider": "zhipu",
        "total_balance": total,
        "balance_details": details,
        "primary_currency": primary_currency,
        "balance_unavailable": False,
    }


def convert_to_usd(balance: float, currency: str, rate: float = DEFAULT_USD_CNY_RATE) -> float:
    if currency.upper() == "CNY":
        return balance / rate if rate > 0 else 0
    return balance


def convert_to_cny(balance: float, currency: str, rate: float = DEFAULT_USD_CNY_RATE) -> float:
    if currency.upper() == "USD":
        return balance * rate
    return balance


def format_balance_log(result: dict, usd_cny_rate: float = DEFAULT_USD_CNY_RATE) -> str:
    if not result.get("valid"):
        return result.get("reason", "?")

    if result.get("balance_unavailable"):
        return "valid (balance N/A)"

    primary_currency = result.get("primary_currency", "CNY")
    total_balance = result.get("total_balance", 0.0)
    usd_eq = convert_to_usd(total_balance, primary_currency, usd_cny_rate)
    cny_eq = convert_to_cny(total_balance, primary_currency, usd_cny_rate)
    return f"{primary_currency} {total_balance:.4f} (≈${usd_eq:.2f} / ¥{cny_eq:.2f})"


def load_queries(path: str = "queries_v4.txt") -> list[str]:
    query_file = Path(path)
    if not query_file.exists():
        return BUILTIN_QUERIES

    queries = []
    for line in query_file.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith("#"):
            queries.append(stripped)
    return queries or BUILTIN_QUERIES


class ScannerEngine:
    def __init__(
        self,
        concurrency: int = 8,
        timeout: int = 20,
        search_delay: float = 4.0,
        scan_pages: int = 2,
        max_duration: int = 0,
        max_valid_keys: int = 0,
        output_dir: str = "results",
        usd_cny_rate: float = DEFAULT_USD_CNY_RATE,
        progress_callback: Callable[[int, int, str], None] | None = None,
        sources: list[str] | None = None,
        auto_disclose: bool | None = None,
        disclose_dry_run: bool | None = None,
        disclose_max_repo_age_days: int | None = None,
        state_db: str = "results/state.db",
        resume: bool = False,
        use_state: bool = True,
    ):
        self.concurrency = concurrency
        self.timeout = timeout
        self.search_delay = search_delay
        self.scan_pages = scan_pages
        self.max_duration = max_duration
        self.max_valid_keys = max_valid_keys
        self.output_dir = output_dir
        self.usd_cny_rate = usd_cny_rate
        self.progress_callback = progress_callback or (lambda *_args: None)
        self.sources = list(sources) if sources else list(DEFAULT_SOURCES)
        self._start_time = 0.0

        # Responsible-disclosure (off by default; dry-run by default when enabled).
        # Resolved from --disclose / --disclose-send flags + env unless the caller
        # passes explicit values, so every scan script opts in the same way.
        if auto_disclose is None or disclose_dry_run is None or disclose_max_repo_age_days is None:
            from disclosure import disclose_options

            _auto, _dry, _max_age = disclose_options()
            if auto_disclose is None:
                auto_disclose = _auto
            if disclose_dry_run is None:
                disclose_dry_run = _dry
            if disclose_max_repo_age_days is None:
                disclose_max_repo_age_days = _max_age
        self.auto_disclose = auto_disclose
        self._discloser = None
        if auto_disclose:
            from disclosure import GitHubDiscloser

            self._discloser = GitHubDiscloser(
                token=ScannerEngine.get_gh_token(),
                dry_run=disclose_dry_run,
                dedup_path=os.path.join(self.output_dir, "disclosed.json"),
                max_repo_age_days=disclose_max_repo_age_days,
                log=lambda msg, *_a, **_k: self.log(msg),
            )

        # Durable state is created lazily (see _ensure_store) so constructing an
        # engine never touches disk; run()/verify_keys() open it on first use.
        self.state_db = state_db
        self.resume = resume
        self.use_state = use_state
        self._store: StateStore | None = None
        self._run_id: str | None = None
        self._rate_limiter: RateLimiter | None = None
        self._verify_limiter: RateLimiter | None = None

    def log(self, message: str):
        stamp = time.strftime("%m-%d %H:%M:%S")
        print(f"[{stamp}] {message}", flush=True)

    @staticmethod
    def get_gh_token() -> str:
        """Resolve a GitHub token from env vars or the gh CLI (empty if none)."""
        for env_var in ("GH_TOKEN", "GITHUB_TOKEN"):
            token = os.environ.get(env_var, "")
            if token:
                return token
        try:
            result = subprocess.run(
                ["gh", "auth", "token"],
                capture_output=True,
                timeout=5,
                encoding="utf-8",
                errors="replace",
            )
            if result.returncode == 0 and result.stdout.strip():
                return result.stdout.strip()
        except Exception:
            pass
        return ""

    def _should_stop(self, valid_count: int = 0) -> bool:
        if self.max_valid_keys > 0 and valid_count >= self.max_valid_keys:
            return True
        if self.max_duration > 0 and self._start_time and time.time() - self._start_time >= self.max_duration:
            return True
        return False

    def _ensure_store(self) -> StateStore:
        if self._store is None:
            self._store = StateStore(path=self.state_db, use_state=self.use_state)
            self._rate_limiter = RateLimiter("github_search", store=self._store)
            self._verify_limiter = RateLimiter("zhipu_verify", store=self._store)
        return self._store

    def _config_sig(self, code_queries: list[str]) -> str:
        payload = json.dumps(
            {"sources": sorted(self.sources), "pages": self.scan_pages, "queries": list(code_queries)},
            sort_keys=True,
        )
        return hashlib.md5(payload.encode(), usedforsecurity=False).hexdigest()

    async def search_github_code(self, queries: list[str]) -> list[dict]:
        scanner = GitHubCodeScanner(concurrency=self.concurrency, timeout=self.timeout, pages=self.scan_pages)
        discovered: list[dict] = []

        for idx, query in enumerate(queries, start=1):
            if self._should_stop():
                break

            self.log(f"search [{idx}/{len(queries)}] github_code: {query}")
            results = await scanner.search(query)
            discovered.extend(results)
            self.progress_callback(idx, len(queries), "search")

            if idx < len(queries) and self.search_delay > 0:
                await asyncio.sleep(self.search_delay)

        return dedup_results(discovered)

    def _build_scanner(self, source: str):
        if source == "github_commits":
            return GitHubCommitsScanner(concurrency=self.concurrency, timeout=self.timeout, pages=self.scan_pages)
        if source == "github_issues":
            return GitHubIssuesScanner(concurrency=self.concurrency, timeout=self.timeout, pages=self.scan_pages)
        return GitHubCodeScanner(concurrency=self.concurrency, timeout=self.timeout, pages=self.scan_pages)

    def _queries_for_source(self, source: str, code_queries: list[str]) -> list[str]:
        # Code search uses the rich filename:-qualified library; the commit/issue
        # search APIs only accept free text, so they get the keyword set.
        return code_queries if source == "github_code" else KEYWORD_QUERIES

    async def _search_all(self, code_queries: list[str]) -> list[dict]:
        discovered: list[dict] = []
        for source in self.sources:
            if self._should_stop():
                break
            scanner = self._build_scanner(source)
            queries = self._queries_for_source(source, code_queries)
            for idx, query in enumerate(queries, start=1):
                if self._should_stop():
                    break
                self.log(f"search [{source}] [{idx}/{len(queries)}] {query}")
                discovered.extend(await scanner.search(query))
                self.progress_callback(idx, len(queries), f"search:{source}")
                if idx < len(queries) and self.search_delay > 0:
                    await asyncio.sleep(self.search_delay)
        return dedup_results(discovered)

    def _group_keys(self, results: list[dict]) -> dict:
        grouped: dict[str, dict] = {}
        for result in dedup_results(results):
            key = result.get("key", "")
            if is_bad_key(key):
                continue
            grouped.setdefault(key, {"repos": []})["repos"].append(
                {
                    "source": result.get("source", ""),
                    "repo": result.get("repo", ""),
                    "file": result.get("file", ""),
                    "url": result.get("url", ""),
                }
            )
        return grouped

    async def _request_verify(self, session: aiohttp.ClientSession, api_key: str, path: str) -> tuple[int, dict | None]:
        url = f"{PROVIDER_CONFIG['base']}{path}"
        headers = {"Authorization": f"Bearer {api_key}"}
        try:
            async with session.get(url, headers=headers, timeout=aiohttp.ClientTimeout(total=self.timeout)) as resp:
                data = None
                if resp.content_type == "application/json":
                    data = await resp.json()
                return resp.status, data
        except asyncio.TimeoutError:
            return -1, None
        except Exception as exc:
            return -2, {"error": str(exc)[:80]}

    async def _verify_one(self, session: aiohttp.ClientSession, api_key: str, semaphore: asyncio.Semaphore) -> dict:
        # Liveness check ONLY. We authenticate just enough (a read-only models
        # list) to confirm the key is active, so its owner can be notified — see
        # disclosure.py. We deliberately do NOT call /user/balance or any billing
        # endpoint: responsible disclosure must never inspect a third party's
        # account, credit, or billing state using their exposed credential.
        async with semaphore:
            status, data = await self._request_verify(session, api_key, PROVIDER_CONFIG["verify_url"])
            if status == 200 and isinstance(data, dict):
                return parse_zhipu_models_response(data)
            if status == 401:
                return {"valid": False, "provider": "zhipu", "reason": "invalid_key"}
            if status == 429:
                return {"valid": False, "provider": "zhipu", "reason": "rate_limited"}
            if status == 402:
                return {"valid": False, "provider": "zhipu", "reason": "insufficient_balance"}
            if status == -1:
                return {"valid": False, "provider": "zhipu", "reason": "timeout"}
            if status == -2 and isinstance(data, dict):
                return {"valid": False, "provider": "zhipu", "reason": data.get("error", "request_error")}
            return {"valid": False, "provider": "zhipu", "reason": f"HTTP_{status}"}

    def _maybe_disclose(self, key: str, info: dict, result: dict) -> None:
        """Open a responsible-disclosure issue for one live key (best-effort).

        No-op unless --disclose / --disclose-send (or env) enabled it. Never
        raises: a disclosure failure must not abort the scan.
        """
        if not self.auto_disclose or self._discloser is None:
            return
        finding = {
            "key": key,
            "provider": result.get("provider", "zhipu"),
            "repos": info.get("repos", []),
        }
        try:
            res = self._discloser.disclose(finding)
            detail = " ".join(
                part for part in (res.get("status", "?"), res.get("repo", ""), res.get("issue_url", "")) if part
            )
            self.log(f"  [DISCLOSE] {detail}")
        except Exception as exc:  # never abort the scan on disclosure failure
            self.log(f"  [DISCLOSE] error: {str(exc)[:120]}")

    async def _verify_all_async(self, grouped_keys: dict) -> list[dict]:
        semaphore = asyncio.Semaphore(self.concurrency)
        items = list(grouped_keys.items())
        results: list[dict] = []
        valid_count = 0

        async with aiohttp.ClientSession() as session:
            for start in range(0, len(items), self.concurrency):
                if self._should_stop(valid_count):
                    break

                batch = items[start : start + self.concurrency]
                verified = await asyncio.gather(
                    *(self._verify_one(session, key, semaphore) for key, _info in batch)
                )

                for (key, info), result in zip(batch, verified):
                    if result.get("valid"):
                        valid_count += 1
                        self.log(f"verify {redact_key(key)} -> {format_balance_log(result, self.usd_cny_rate)}")
                        self._maybe_disclose(key, info, result)
                    else:
                        self.log(f"verify {redact_key(key)} -> {result.get('reason', '?')}")

                    total_balance = result.get("total_balance", 0.0)
                    primary_currency = result.get("primary_currency", "CNY")
                    results.append(
                        {
                            "key": key,
                            "key_redacted": redact_key(key),
                            "valid": result.get("valid", False),
                            "balance": total_balance,
                            "balance_details": result.get("balance_details", []),
                            "primary_currency": primary_currency,
                            "balance_usd": convert_to_usd(total_balance, primary_currency, self.usd_cny_rate),
                            "balance_cny": convert_to_cny(total_balance, primary_currency, self.usd_cny_rate),
                            "balance_unavailable": result.get("balance_unavailable", False),
                            "reason": result.get("reason", ""),
                            "provider": result.get("provider", "zhipu"),
                            "provider_note": result.get("provider_note", ""),
                            "repos": info["repos"],
                            "verified_at": datetime.now().isoformat(),
                        }
                    )
                    self.progress_callback(len(results), len(items), "verify")

        return results

    def verify_keys(self, grouped_keys: dict) -> list[dict]:
        if not grouped_keys:
            return []
        return asyncio.run(self._verify_all_async(grouped_keys))

    def run(self, queries: list[str] | None = None) -> list[dict]:
        self._start_time = time.time()
        query_list = queries or load_queries()
        discovered = asyncio.run(self._search_all(query_list))
        self.log(f"discovered {len(discovered)} candidate locations across {len(self.sources)} source(s)")

        grouped = self._group_keys(discovered)
        self.log(f"extracted {len(grouped)} unique candidate keys")

        results = self.verify_keys(grouped)
        self.save_results(results)
        return results

    @staticmethod
    def sort_results(results: list[dict]) -> list[dict]:
        # Valid (live) keys first, then stable by key. We intentionally do NOT
        # rank by balance: this tool triages who to notify, not which credential
        # is most valuable.
        return sorted(
            results,
            key=lambda item: (not item.get("valid", False), item.get("key", "")),
        )

    def save_results(self, results: list[dict], fmt: str = "all") -> list[dict]:
        os.makedirs(self.output_dir, exist_ok=True)
        sorted_results = self.sort_results(results)

        if fmt in {"all", "json"}:
            path = os.path.join(self.output_dir, f"{RESULT_BASENAME}.json")
            with open(path, "w", encoding="utf-8") as handle:
                json.dump(sorted_results, handle, ensure_ascii=False, indent=2)

        if fmt in {"all", "csv"}:
            path = os.path.join(self.output_dir, f"{RESULT_BASENAME}.csv")
            with open(path, "w", encoding="utf-8", newline="") as handle:
                writer = csv.writer(handle)
                writer.writerow(
                    [
                        "key_redacted",
                        "valid",
                        "balance",
                        "currency",
                        "balance_usd",
                        "balance_cny",
                        "provider",
                        "reason",
                        "locations",
                        "verified_at",
                    ]
                )
                for result in sorted_results:
                    writer.writerow(
                        [
                            result.get("key_redacted", redact_key(result.get("key", ""))),
                            result.get("valid", False),
                            result.get("balance", 0),
                            result.get("primary_currency", ""),
                            result.get("balance_usd", 0),
                            result.get("balance_cny", 0),
                            result.get("provider", ""),
                            result.get("reason", ""),
                            len(result.get("repos", [])),
                            result.get("verified_at", ""),
                        ]
                    )

        if fmt in {"all", "md"}:
            path = os.path.join(self.output_dir, f"{RESULT_BASENAME}.md")
            with open(path, "w", encoding="utf-8") as handle:
                valid = [item for item in sorted_results if item.get("valid")]
                positive = [item for item in valid if item.get("balance_usd", 0) > 0]
                total_usd = sum(item.get("balance_usd", 0) for item in positive)
                total_cny = sum(item.get("balance_cny", 0) for item in positive)
                handle.write("# ZhipuGLM Hunter Results\n\n")
                handle.write(f"- Total candidate keys: {len(sorted_results)}\n")
                handle.write(f"- Valid keys: {len(valid)}\n")
                handle.write(f"- Positive balance: {len(positive)}\n")
                handle.write(f"- Total balance: ${total_usd:.2f} / ¥{total_cny:.2f}\n\n")
                handle.write("| Key | Valid | Balance | USD | CNY | Provider | Locations | Reason |\n")
                handle.write("| --- | --- | ---: | ---: | ---: | --- | ---: | --- |\n")
                for result in sorted_results:
                    currency = result.get("primary_currency", "CNY")
                    balance = result.get("balance", 0)
                    handle.write(
                        f"| {result.get('key_redacted', redact_key(result.get('key', '')))} "
                        f"| {result.get('valid', False)} "
                        f"| {currency} {balance:.4f} "
                        f"| ${result.get('balance_usd', 0):.2f} "
                        f"| ¥{result.get('balance_cny', 0):.2f} "
                        f"| {result.get('provider', '')} "
                        f"| {len(result.get('repos', []))} "
                        f"| {result.get('reason', '') or result.get('provider_note', '')} |\n"
                    )

        return sorted_results


__all__ = [
    "BUILTIN_QUERIES",
    "BALANCE_PATH",
    "DEFAULT_USD_CNY_RATE",
    "PROVIDER_CONFIG",
    "RESULT_BASENAME",
    "VERIFY_PATH",
    "ZHIPU_API_BASE",
    "ScannerEngine",
    "convert_to_cny",
    "convert_to_usd",
    "extract_keys",
    "format_balance_log",
    "is_bad_key",
    "load_queries",
    "parse_zhipu_balance",
    "parse_zhipu_models_response",
    "redact_key",
]
