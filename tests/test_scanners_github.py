"""Network-free tests for the GitHub commit/issue scanners.

Only the pure item->results parsers are exercised; the HTTP search loops are
not called, so the suite stays offline.
"""

from scanners.base import auto_github_token, github_api_headers
from scanners.github_commits import GitHubCommitsScanner
from scanners.github_issues import GitHubIssuesScanner

VALID_KEY = "a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6.AbCdEfGhIjKlMnOp"


# --- shared helpers ----------------------------------------------------------


def test_github_api_headers_sets_auth_and_accept():
    headers = github_api_headers("tok123")
    assert headers["Authorization"] == "Bearer tok123"
    assert headers["Accept"] == "application/vnd.github+json"
    assert "User-Agent" in headers


def test_github_api_headers_without_token_has_no_auth():
    assert "Authorization" not in github_api_headers("")


def test_auto_github_token_is_callable_and_returns_str():
    # Should never raise even when gh is absent; just returns a string.
    assert isinstance(auto_github_token(_runner=lambda *a, **k: None), str)


# --- issues scanner ----------------------------------------------------------


def test_issue_repo_parsed_from_repository_url():
    assert GitHubIssuesScanner._repo_from_api_url("https://api.github.com/repos/octo/widget") == "octo/widget"
    assert GitHubIssuesScanner._repo_from_api_url("garbage") == ""


def test_issue_item_extracts_key_with_repo_and_url():
    scanner = GitHubIssuesScanner(token="t")
    item = {
        "title": "help with config",
        "body": f"my key is {VALID_KEY} please advise",
        "html_url": "https://github.com/octo/widget/issues/3",
        "repository_url": "https://api.github.com/repos/octo/widget",
    }
    results = scanner._keys_from_issue_item(item)
    assert len(results) == 1
    assert results[0]["key"] == VALID_KEY
    assert results[0]["repo"] == "octo/widget"
    assert results[0]["url"] == "https://github.com/octo/widget/issues/3"
    assert results[0]["source"] == "github_issues"


def test_issue_item_drops_placeholder_keys_and_handles_null_body():
    scanner = GitHubIssuesScanner(token="t")
    item = {"title": "example", "body": None, "repository_url": "", "html_url": ""}
    assert scanner._keys_from_issue_item(item) == []


# --- commits scanner ---------------------------------------------------------


def test_commit_extracts_key_from_patch_with_repo_and_file():
    scanner = GitHubCommitsScanner(token="t")
    item = {
        "commit": {"message": "add config"},
        "repository": {"full_name": "octo/widget"},
        "html_url": "https://github.com/octo/widget/commit/abc",
    }
    detail = {"files": [{"filename": ".env", "patch": f"+ZHIPU_API_KEY={VALID_KEY}"}]}
    results = scanner._keys_from_commit(item, detail)
    assert len(results) == 1
    assert results[0]["key"] == VALID_KEY
    assert results[0]["repo"] == "octo/widget"
    assert results[0]["file"] == ".env"
    assert results[0]["url"] == "https://github.com/octo/widget/commit/abc"
    assert results[0]["source"] == "github_commits"


def test_commit_extracts_key_from_message_without_detail():
    scanner = GitHubCommitsScanner(token="t")
    item = {
        "commit": {"message": f"oops committed {VALID_KEY}"},
        "repository": {"full_name": "octo/widget"},
        "html_url": "https://github.com/octo/widget/commit/abc",
    }
    results = scanner._keys_from_commit(item, None)
    assert len(results) == 1
    assert results[0]["key"] == VALID_KEY
    assert results[0]["file"] == ""


def test_commit_handles_missing_fields():
    scanner = GitHubCommitsScanner(token="t")
    assert scanner._keys_from_commit({}, None) == []
