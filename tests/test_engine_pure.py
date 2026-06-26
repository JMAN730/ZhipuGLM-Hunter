"""Network-free tests for scanner_engine.py."""

from pathlib import Path

from scanner_engine import (
    BALANCE_PATH,
    PROVIDER_CONFIG,
    RESULT_BASENAME,
    ScannerEngine,
    convert_to_cny,
    convert_to_usd,
    format_balance_log,
    load_queries,
    parse_zhipu_balance,
    parse_zhipu_models_response,
)

VALID_KEY = "a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6.AbCdEfGhIjKlMnOp"


def test_provider_config_is_zhipu_models_endpoint():
    assert PROVIDER_CONFIG["name"] == "zhipu"
    assert PROVIDER_CONFIG["base"] == "https://open.bigmodel.cn/api/paas/v4"
    assert PROVIDER_CONFIG["verify_url"] == "/models"
    assert PROVIDER_CONFIG["balance_url"] == BALANCE_PATH


def test_parse_zhipu_models_response_valid():
    result = parse_zhipu_models_response({"data": [{"id": "glm-4-flash"}]})
    assert result["valid"] is True
    assert result["provider"] == "zhipu"
    assert result["balance_unavailable"] is True


def test_parse_zhipu_balance_sums_cny_and_usd():
    result = parse_zhipu_balance(
        {
            "balance_infos": [
                {
                    "currency": "CNY",
                    "total_balance": "12.5",
                    "granted_balance": "2.5",
                    "tipped_balance": "0",
                },
                {
                    "currency": "USD",
                    "total_balance": "1.0",
                    "granted_balance": "0",
                    "tipped_balance": "0",
                },
            ]
        }
    )
    assert result["valid"] is True
    assert result["total_balance"] == 13.5
    assert result["primary_currency"] == "USD"
    assert len(result["balance_details"]) == 2


def test_format_balance_log_shows_amounts():
    message = format_balance_log(
        {
            "valid": True,
            "total_balance": 10.0,
            "primary_currency": "CNY",
            "balance_unavailable": False,
        }
    )
    assert "CNY 10.0000" in message
    assert "$" in message


def test_convert_currency_helpers():
    assert convert_to_usd(7.25, "CNY") == 1.0
    assert convert_to_cny(1.0, "USD") == 7.25


def test_parse_zhipu_models_response_invalid_shape():
    result = parse_zhipu_models_response({"error": {"message": "bad key"}})
    assert result["valid"] is False
    assert result["reason"] == "unexpected_response"


def test_load_queries_ignores_comments_and_blank_lines(tmp_path: Path):
    query_file = tmp_path / "queries.txt"
    query_file.write_text("# comment\n\nzhipu filename:py\n", encoding="utf-8")
    assert load_queries(str(query_file)) == ["zhipu filename:py"]


def test_group_keys_collects_repos_and_drops_invalid():
    engine = ScannerEngine()
    grouped = engine._group_keys(
        [
            {"key": VALID_KEY, "source": "github", "repo": "o/r", "file": ".env", "url": "u"},
            {"key": "sk-not-a-zhipu-key", "source": "github", "repo": "o/r2", "file": ".env", "url": "u2"},
        ]
    )
    assert list(grouped) == [VALID_KEY]
    assert grouped[VALID_KEY]["repos"][0]["repo"] == "o/r"


def test_save_results_writes_expected_files(tmp_path: Path):
    engine = ScannerEngine(output_dir=str(tmp_path))
    result = {
        "key": VALID_KEY,
        "key_redacted": "a1b2c3d4...c5d6.AbCd...MnOp",
        "valid": True,
        "balance": 12.5,
        "balance_details": [],
        "primary_currency": "CNY",
        "balance_usd": 1.72,
        "balance_cny": 12.5,
        "provider": "zhipu",
        "reason": "",
        "repos": [{"repo": "o/r"}],
        "verified_at": "2026-06-25T00:00:00",
    }
    engine.save_results([result])

    assert (tmp_path / f"{RESULT_BASENAME}.json").exists()
    assert (tmp_path / f"{RESULT_BASENAME}.csv").exists()
    assert (tmp_path / f"{RESULT_BASENAME}.md").exists()
    assert VALID_KEY not in (tmp_path / f"{RESULT_BASENAME}.md").read_text(encoding="utf-8")
