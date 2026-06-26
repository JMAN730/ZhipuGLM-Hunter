"""Responsible-disclosure: open a GitHub issue on repos where a *live* API key
was found, telling the owner to rotate/revoke it.

The issue NEVER contains the live secret — only a masked prefix, the file path,
and the location URL. All posting is gated by the caller (off by default,
dry-run by default). This module owns issue rendering, deduplication, and
rate limiting; it knows nothing about scanning.
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone

# Where to rotate keys, per provider. Generic fallback for anything unknown.
PROVIDER_ROTATE_URLS = {
    "zhipu": "https://open.bigmodel.cn/usercenter/apikeys",
    "zhipuai": "https://open.bigmodel.cn/usercenter/apikeys",
    "bigmodel": "https://open.bigmodel.cn/usercenter/apikeys",
    "glm": "https://open.bigmodel.cn/usercenter/apikeys",
    "deepseek": "https://platform.deepseek.com/api_keys",
    "openai": "https://platform.openai.com/api-keys",
}
GENERIC_ROTATE_URL = "your provider's API key dashboard"

ISSUE_TITLE = "Exposed API credential found in this repository"


def disclose_options(argv=None, env=None):
    """Resolve disclosure options from CLI flags + env.

    Universal opt-in for every scan script, off by default:

    - ``--disclose``        → enable, dry-run (prints what it WOULD post)
    - ``--disclose-send``   → enable AND actually post issues
    - env ``ZHIPU_DISCLOSE`` / ``ZHIPU_DISCLOSE_SEND`` as fallbacks
    - ``--disclose-max-repo-age-days N`` / env
      ``ZHIPU_DISCLOSE_MAX_REPO_AGE_DAYS`` gates disclosure to repos
      pushed within the last N days.

    An explicit CLI flag wins over env. ``--disclose`` (dry-run) wins over a
    ``--disclose-send`` env var, so dry-run is never silently upgraded to real
    posting.
    """
    import sys

    argv = sys.argv[1:] if argv is None else argv
    env = os.environ if env is None else env

    cli_dry = "--disclose" in argv
    cli_send = "--disclose-send" in argv
    if cli_dry or cli_send:
        # explicit flag present: dry-run flag takes precedence over send
        return True, not (cli_send and not cli_dry), _age_limit_days(argv, env)

    if str(env.get("ZHIPU_DISCLOSE_SEND", "")).strip() not in ("", "0", "false", "False"):
        return True, False, _age_limit_days(argv, env)
    if str(env.get("ZHIPU_DISCLOSE", "")).strip() not in ("", "0", "false", "False"):
        return True, True, _age_limit_days(argv, env)
    return False, True, _age_limit_days(argv, env)


def _age_limit_days(argv, env):
    """Parse optional repo-age gate from CLI/env. Invalid values disable it."""
    flag = "--disclose-max-repo-age-days"
    value = None
    for i, arg in enumerate(argv):
        if arg == flag and i + 1 < len(argv):
            value = argv[i + 1]
            break
        if arg.startswith(flag + "="):
            value = arg.split("=", 1)[1]
            break
    if value is None:
        value = env.get("ZHIPU_DISCLOSE_MAX_REPO_AGE_DAYS")
    try:
        days = int(value) if value not in (None, "") else 0
    except (TypeError, ValueError):
        return None
    return days if days > 0 else None


def _mask_key(key: str, visible: int = 5) -> str:
    """Return only the first `visible` chars of a key plus an ellipsis.

    Never reveals enough of the key to be usable.
    """
    if not key:
        return ""
    return key[:visible] + "…"


class GitHubDiscloser:
    """Opens one disclosure issue per affected repo (deduplicated, rate-limited).

    Call `.disclose(finding)` with a single validated finding of the shape
    produced by ScannerEngine: ``{key, provider, repos: [{repo, file, url}], ...}``.
    """

    def __init__(
        self,
        token,
        *,
        dry_run: bool = True,
        rate_limit_s: float = 30,
        max_per_run: int = 25,
        dedup_path: str = "results/disclosed.json",
        max_repo_age_days: int = None,
        log=print,
        sleep=time.sleep,
        now=time.time,
    ):
        self.token = token
        self.dry_run = dry_run
        self.rate_limit_s = rate_limit_s
        self.max_per_run = max_per_run
        self.dedup_path = dedup_path
        self.max_repo_age_days = max_repo_age_days
        self.log = log
        self._sleep = sleep
        self._now = now

        self._disclosed = self._load_dedup()  # repo -> {status, issue_url, disclosed_at}
        self._posted_count = 0
        self._last_post_ts = 0.0
        self._stopped = False  # set when GitHub returns a secondary rate-limit (403)

    # --- dedup persistence ---------------------------------------------------

    def _load_dedup(self) -> dict:
        try:
            with open(self.dedup_path, encoding="utf-8") as f:
                data = json.load(f)
            return data if isinstance(data, dict) else {}
        except (FileNotFoundError, ValueError, OSError):
            return {}

    def _save_dedup(self) -> None:
        try:
            os.makedirs(os.path.dirname(self.dedup_path) or ".", exist_ok=True)
            with open(self.dedup_path, "w", encoding="utf-8") as f:
                json.dump(self._disclosed, f, indent=2)
        except OSError as e:
            self.log(f"[DISCLOSE] could not write dedup file: {e}", "warning")

    def _record(self, repo: str, status: str, issue_url: str = "", reason: str = "") -> None:
        self._disclosed[repo] = {
            "status": status,
            "issue_url": issue_url,
            "disclosed_at": int(time.time()),
        }
        if reason:
            self._disclosed[repo]["reason"] = reason
        self._save_dedup()

    # --- issue rendering -----------------------------------------------------

    def render_issue(self, finding: dict):
        """Return (title, body) for a finding. Body never includes the full key."""
        loc = (finding.get("repos") or [{}])[0]
        path = loc.get("file", "")
        url = loc.get("url", "")
        provider = (finding.get("provider") or "").lower()
        masked = _mask_key(finding.get("key", ""))
        rotate = PROVIDER_ROTATE_URLS.get(provider, GENERIC_ROTATE_URL)
        provider_label = provider or "API"

        body = (
            f"A scan found what appears to be a **live {provider_label} API key** "
            f"committed to this repository.\n\n"
            f"- File: `{path}`\n"
            f"- Location: {url}\n"
            f"- Key (masked): `{masked}`\n\n"
            f"This key validated as active, which means anyone can use it on your "
            f"account.\n\n"
            f"Please **rotate/revoke it now**, then remove it from git history.\n"
            f"Rotate: {rotate}"
        )
        return ISSUE_TITLE, body

    # --- main entry ----------------------------------------------------------

    def disclose(self, finding: dict) -> dict:
        """Open a disclosure issue for one finding. Returns a result dict.

        status is one of: posted, dry_run, skipped_dedup, skipped_disabled,
        skipped_old_repo, skipped_repo_metadata, rate_limited, error.
        """
        loc = (finding.get("repos") or [{}])[0]
        repo = loc.get("repo", "")
        if not repo or "/" not in repo:
            return {"status": "error", "repo": repo, "reason": "no repo"}

        if repo in self._disclosed:
            return {"status": "skipped_dedup", "repo": repo, "issue_url": self._disclosed[repo].get("issue_url", "")}

        if self._stopped or self._posted_count >= self.max_per_run:
            return {"status": "rate_limited", "repo": repo}

        age_result = self._check_repo_age(repo)
        if age_result is not None:
            return age_result

        title, body = self.render_issue(finding)

        if self.dry_run:
            self.log(f"[DISCLOSE dry-run] would open issue on {repo}: {title}")
            return {"status": "dry_run", "repo": repo}

        self._respect_rate_limit()
        owner, name = repo.split("/", 1)
        try:
            resp = self._post_issue(owner, name, title, body)
        except Exception as e:  # network failure — never abort the scan
            return {"status": "error", "repo": repo, "reason": str(e)}

        code = resp.get("status_code", 0)
        if code == 201:
            self._posted_count += 1
            self._last_post_ts = time.time()
            issue_url = resp.get("html_url", "")
            self._record(repo, "posted", issue_url)
            return {"status": "posted", "repo": repo, "issue_url": issue_url}
        if code in (404, 410):
            # issues disabled / repo gone — record so re-scans don't retry
            self._record(repo, "skipped_disabled")
            return {"status": "skipped_disabled", "repo": repo}
        if code in (403, 429):
            # secondary rate limit — stop disclosing for the rest of the run
            self._stopped = True
            return {"status": "rate_limited", "repo": repo}
        return {"status": "error", "repo": repo, "reason": f"http {code}"}

    def _respect_rate_limit(self) -> None:
        if self._last_post_ts and self.rate_limit_s > 0:
            elapsed = time.time() - self._last_post_ts
            if elapsed < self.rate_limit_s:
                self._sleep(self.rate_limit_s - elapsed)

    def _check_repo_age(self, repo: str):
        if not self.max_repo_age_days:
            return None
        pushed_at = self._get_repo_pushed_at(repo)
        if not pushed_at:
            return {"status": "skipped_repo_metadata", "repo": repo, "reason": "could not determine repo pushed_at"}
        pushed_ts = _parse_github_timestamp(pushed_at)
        if pushed_ts is None:
            return {"status": "skipped_repo_metadata", "repo": repo, "reason": f"invalid pushed_at {pushed_at}"}
        age_days = max(0, int((self._now() - pushed_ts) // 86400))
        if age_days > self.max_repo_age_days:
            self._record(repo, "skipped_old_repo", reason=f"pushed_at={pushed_at}")
            return {"status": "skipped_old_repo", "repo": repo, "age_days": age_days, "pushed_at": pushed_at}
        return None

    def _get_repo_pushed_at(self, repo: str) -> str:
        """Return GitHub repo pushed_at timestamp, or empty string on failure."""
        owner, name = repo.split("/", 1)
        url = f"https://api.github.com/repos/{owner}/{name}"
        req = urllib.request.Request(url, method="GET")
        if self.token:
            req.add_header("Authorization", f"Bearer {self.token}")
        req.add_header("Accept", "application/vnd.github+json")
        req.add_header("User-Agent", "ZhipuGLM-Hunter-Disclosure")
        try:
            with urllib.request.urlopen(req, timeout=20) as r:
                data = json.loads(r.read().decode("utf-8"))
                return data.get("pushed_at", "")
        except (urllib.error.HTTPError, OSError, ValueError):
            return ""

    # --- HTTP (monkeypatched in tests) ---------------------------------------

    def _post_issue(self, owner: str, repo: str, title: str, body: str) -> dict:
        """POST a new issue. Returns {status_code, html_url}. Real network call."""
        url = f"https://api.github.com/repos/{owner}/{repo}/issues"
        payload = json.dumps({"title": title, "body": body}).encode("utf-8")
        req = urllib.request.Request(url, data=payload, method="POST")
        req.add_header("Authorization", f"Bearer {self.token}")
        req.add_header("Accept", "application/vnd.github+json")
        req.add_header("User-Agent", "ZhipuGLM-Hunter-Disclosure")
        try:
            with urllib.request.urlopen(req, timeout=20) as r:
                data = json.loads(r.read().decode("utf-8"))
                return {"status_code": r.status, "html_url": data.get("html_url", "")}
        except urllib.error.HTTPError as e:
            return {"status_code": e.code, "html_url": ""}


def _parse_github_timestamp(value: str):
    try:
        dt = datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ")
    except (TypeError, ValueError):
        return None
    return dt.replace(tzinfo=timezone.utc).timestamp()
