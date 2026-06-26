"""Tests for disclosure.GitHubDiscloser.

Pure / no real network: the HTTP layer (`_post_issue`) is monkeypatched, so no
GitHub issues are ever created by the test suite.
"""

import json

import pytest

from disclosure import GitHubDiscloser, _mask_key, disclose_options

# Realistic Zhipu key shape: 32 hex + "." + secret.
FULL_KEY = "a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6.AbCdEfGhIjKlMnOp"


def _finding(repo="acme/widgets", key=FULL_KEY, provider="zhipu"):
    return {
        "key": key,
        "provider": provider,
        "balance_usd": 0.0,
        "repos": [{"repo": repo, "file": "src/config.py", "url": f"https://github.com/{repo}/blob/main/src/config.py"}],
        "verified_at": 1700000000,
    }


def _discloser(tmp_path, **kw):
    kw.setdefault("dry_run", False)
    kw.setdefault("rate_limit_s", 0)
    kw.setdefault("sleep", lambda *_: None)
    kw.setdefault("log", lambda *a, **k: None)
    return GitHubDiscloser(token="t", dedup_path=str(tmp_path / "disclosed.json"), **kw)


# --- masking -----------------------------------------------------------------


def test_mask_shows_only_prefix():
    assert _mask_key(FULL_KEY) == "a1b2c…"
    assert FULL_KEY[5:] not in _mask_key(FULL_KEY)


def test_mask_empty():
    assert _mask_key("") == ""


# --- issue rendering ---------------------------------------------------------


def test_body_has_path_masked_prefix_and_rotation_url(tmp_path):
    d = _discloser(tmp_path)
    title, body = d.render_issue(_finding())
    assert "credential" in title.lower()
    assert "src/config.py" in body
    assert "a1b2c…" in body
    assert "bigmodel.cn" in body  # provider rotation url


def test_body_never_contains_full_key(tmp_path):
    d = _discloser(tmp_path)
    _title, body = d.render_issue(_finding())
    assert FULL_KEY not in body
    assert FULL_KEY[5:] not in body


# --- dry run -----------------------------------------------------------------


def test_dry_run_does_not_post(tmp_path):
    posts = []
    d = _discloser(tmp_path, dry_run=True)
    d._post_issue = lambda *a, **k: posts.append(a) or {"status_code": 201}
    res = d.disclose(_finding())
    assert res["status"] == "dry_run"
    assert posts == []


# --- dedup -------------------------------------------------------------------


def test_dedup_skips_second_time_same_repo(tmp_path):
    calls = []
    d = _discloser(tmp_path)
    d._post_issue = lambda *a, **k: (
        calls.append(1) or {"status_code": 201, "html_url": "https://github.com/acme/widgets/issues/1"}
    )
    first = d.disclose(_finding())
    second = d.disclose(_finding())
    assert first["status"] == "posted"
    assert second["status"] == "skipped_dedup"
    assert len(calls) == 1


def test_dedup_persists_across_instances(tmp_path):
    d = _discloser(tmp_path)
    d._post_issue = lambda *a, **k: {"status_code": 201, "html_url": "https://github.com/acme/widgets/issues/1"}
    d.disclose(_finding())
    d2 = _discloser(tmp_path)
    d2._post_issue = lambda *a, **k: pytest.fail("should not post for known repo")
    assert d2.disclose(_finding())["status"] == "skipped_dedup"


# --- issues disabled ---------------------------------------------------------


def test_issues_disabled_is_skipped_and_recorded(tmp_path):
    d = _discloser(tmp_path)
    d._post_issue = lambda *a, **k: {"status_code": 410}
    res = d.disclose(_finding())
    assert res["status"] == "skipped_disabled"
    # recorded so a re-scan does not retry
    d2 = _discloser(tmp_path)
    d2._post_issue = lambda *a, **k: pytest.fail("should not retry disabled repo")
    assert d2.disclose(_finding())["status"] == "skipped_dedup"


# --- rate limiting -----------------------------------------------------------


def test_max_per_run_cap(tmp_path):
    d = _discloser(tmp_path, max_per_run=2)
    d._post_issue = lambda owner, repo, *a, **k: {
        "status_code": 201,
        "html_url": f"https://github.com/{owner}/{repo}/issues/1",
    }
    r1 = d.disclose(_finding(repo="a/one"))
    r2 = d.disclose(_finding(repo="b/two"))
    r3 = d.disclose(_finding(repo="c/three"))
    assert [r1["status"], r2["status"]] == ["posted", "posted"]
    assert r3["status"] == "rate_limited"


def test_secondary_rate_limit_stops_run(tmp_path):
    d = _discloser(tmp_path, max_per_run=10)
    d._post_issue = lambda *a, **k: {"status_code": 403}
    assert d.disclose(_finding(repo="a/one"))["status"] == "rate_limited"
    # subsequent calls are short-circuited without posting
    d._post_issue = lambda *a, **k: pytest.fail("run should be stopped after 403")
    assert d.disclose(_finding(repo="b/two"))["status"] == "rate_limited"


# --- repo age gate -----------------------------------------------------------


def test_repo_age_gate_posts_recent_repo(tmp_path):
    d = _discloser(tmp_path, max_repo_age_days=30, now=lambda: 1702592000)
    d._get_repo_pushed_at = lambda repo: "2023-12-01T00:00:00Z"
    d._post_issue = lambda *a, **k: {"status_code": 201, "html_url": "https://github.com/acme/widgets/issues/7"}
    assert d.disclose(_finding())["status"] == "posted"


def test_repo_age_gate_skips_old_repo_and_records(tmp_path):
    d = _discloser(tmp_path, max_repo_age_days=30, now=lambda: 1702592000)
    d._get_repo_pushed_at = lambda repo: "2023-01-01T00:00:00Z"
    d._post_issue = lambda *a, **k: pytest.fail("old repo should not be posted")
    res = d.disclose(_finding())
    assert res["status"] == "skipped_old_repo"

    saved = json.loads((tmp_path / "disclosed.json").read_text())
    assert saved["acme/widgets"]["status"] == "skipped_old_repo"


def test_repo_age_gate_skips_when_metadata_unavailable(tmp_path):
    d = _discloser(tmp_path, max_repo_age_days=30)
    d._get_repo_pushed_at = lambda repo: ""
    d._post_issue = lambda *a, **k: pytest.fail("unknown repo age should not be posted")
    assert d.disclose(_finding())["status"] == "skipped_repo_metadata"


# --- posted result -----------------------------------------------------------


def test_posted_returns_issue_url(tmp_path):
    d = _discloser(tmp_path)
    d._post_issue = lambda *a, **k: {"status_code": 201, "html_url": "https://github.com/acme/widgets/issues/7"}
    res = d.disclose(_finding())
    assert res["status"] == "posted"
    assert res["issue_url"].endswith("/issues/7")
    assert res["repo"] == "acme/widgets"
    # dedup file written
    saved = json.loads((tmp_path / "disclosed.json").read_text())
    assert "acme/widgets" in saved


# --- option parsing ----------------------------------------------------------


def test_options_off_by_default():
    assert disclose_options(argv=[], env={}) == (False, True, None)


def test_disclose_flag_enables_dry_run():
    assert disclose_options(argv=["--disclose"], env={}) == (True, True, None)


def test_disclose_send_flag_enables_real_post():
    assert disclose_options(argv=["--disclose-send"], env={}) == (True, False, None)


def test_env_fallback_enables():
    assert disclose_options(argv=[], env={"ZHIPU_DISCLOSE": "1"}) == (True, True, None)
    assert disclose_options(argv=[], env={"ZHIPU_DISCLOSE_SEND": "1"}) == (True, False, None)


def test_cli_flag_overrides_env_send():
    # explicit --disclose (dry-run) should win even if env asks to send
    assert disclose_options(argv=["--disclose"], env={"ZHIPU_DISCLOSE_SEND": "1"}) == (True, True, None)


def test_cli_age_gate_option():
    assert disclose_options(
        argv=["--disclose-send", "--disclose-max-repo-age-days", "180"],
        env={},
    ) == (True, False, 180)


def test_env_age_gate_option():
    assert disclose_options(
        argv=[],
        env={"ZHIPU_DISCLOSE": "1", "ZHIPU_DISCLOSE_MAX_REPO_AGE_DAYS": "90"},
    ) == (True, True, 90)
