"""Network-free tests for scanner_engine.py."""

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, patch

from scanner_engine import (
    BALANCE_PATH,
    PROVIDER_CONFIG,
    QUOTA_ENDPOINT_BASES,
    RESULT_BASENAME,
    ScannerEngine,
    convert_to_cny,
    convert_to_usd,
    format_balance_display,
    format_balance_log,
    format_money_display,
    load_queries,
    parse_zhipu_balance,
    parse_zhipu_models_response,
    parse_zhipu_quota,
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
    assert "Pay-as-you-go" in result["provider_note"]


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


def test_parse_zhipu_balance_empty_infos_is_zero_cash():
    result = parse_zhipu_balance({"balance_infos": []})
    assert result["valid"] is True
    assert result["total_balance"] == 0.0
    assert result["balance_kind"] == "cash"
    assert result["balance_unavailable"] is False


def test_format_balance_display_quota_and_unavailable():
    assert format_balance_display({"balance_kind": "quota", "balance": 2_500_000}) == "TOKENS 2.5M"
    assert format_balance_display({"balance_unavailable": True}) == "N/A"
    assert format_balance_display({"primary_currency": "CNY", "balance": 1.5}) == "CNY 1.5000"


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


def test_format_balance_log_shows_quota():
    message = format_balance_log(
        {
            "valid": True,
            "total_balance": 2_973_890,
            "balance_kind": "quota",
            "quota_plan": "lite",
            "balance_unavailable": False,
        }
    )
    assert "quota 3.0M tokens remaining" in message
    assert "lite" in message


def test_parse_zhipu_quota_tokens_remaining():
    result = parse_zhipu_quota(
        {
            "success": True,
            "code": 200,
            "data": {
                "level": "lite",
                "limits": [
                    {
                        "type": "TOKENS_LIMIT",
                        "remaining": 29738902,
                        "percentage": 25,
                    }
                ],
            },
        }
    )
    assert result["valid"] is True
    assert result["balance_kind"] == "quota"
    assert result["total_balance"] == 29738902
    assert result["quota_plan"] == "lite"


def test_parse_zhipu_quota_rejects_non_plan_response():
    result = parse_zhipu_quota({"success": False, "code": 1000, "msg": "not coding plan"})
    assert result["valid"] is False


def test_parse_zhipu_quota_derives_remaining_from_usage():
    result = parse_zhipu_quota(
        {
            "success": True,
            "data": {
                "level": "lite",
                "limits": [
                    {
                        "type": "TOKENS_LIMIT",
                        "usage": 1000,
                        "currentValue": 250,
                        "percentage": 75,
                    }
                ],
            },
        }
    )
    assert result["valid"] is True
    assert result["total_balance"] == 750.0


def test_parse_zhipu_quota_uses_percentage_when_tokens_unknown():
    result = parse_zhipu_quota(
        {
            "success": True,
            "data": {
                "level": "lite",
                "limits": [{"type": "TOKENS_LIMIT", "percentage": 1, "unit": 3, "number": 5}],
            },
        }
    )
    assert result["valid"] is True
    assert result["total_balance"] == 0.0
    assert result["quota_used_pct"] == 1.0
    assert result["quota_remaining_pct"] == 99.0
    assert "~99% tokens remaining" in result["provider_note"]


def test_parse_zhipu_quota_picks_most_depleted_tokens_window():
    result = parse_zhipu_quota(
        {
            "success": True,
            "data": {
                "level": "lite",
                "limits": [
                    {"type": "TOKENS_LIMIT", "unit": 3, "number": 5, "percentage": 0},
                    {"type": "TOKENS_LIMIT", "unit": 6, "number": 1, "percentage": 100},
                ],
            },
        }
    )
    assert result["quota_remaining_pct"] == 0.0
    assert result["quota_used_pct"] == 100.0


def test_parse_zhipu_quota_accepts_code_only_success():
    result = parse_zhipu_quota(
        {
            "code": 200,
            "msg": "ok",
            "data": {
                "level": "lite",
                "limits": [{"type": "TOKENS_LIMIT", "remaining": 42}],
            },
        }
    )
    assert result["valid"] is True
    assert result["total_balance"] == 42.0


def test_format_balance_log_shows_quota_percentage():
    message = format_balance_log(
        {
            "valid": True,
            "balance_kind": "quota",
            "quota_plan": "lite",
            "quota_remaining_pct": 99.0,
            "total_balance": 0.0,
            "balance_unavailable": False,
        }
    )
    assert "quota ~99% tokens remaining" in message


def test_format_money_display_unavailable_and_quota():
    assert format_money_display({"balance_unavailable": True}, "balance_usd") == "N/A"
    assert format_money_display({"balance_kind": "quota"}, "balance_cny") == "N/A"
    assert format_money_display({"balance_usd": 1.5}, "balance_usd") == "$1.50"


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


def test_group_keys_from_saved_results_valid_only_and_dedup():
    records = [
        {
            "key": VALID_KEY,
            "valid": True,
            "repos": [{"source": "github_code", "repo": "o/r", "file": ".env", "url": "u1"}],
        },
        {
            "key": VALID_KEY,
            "valid": True,
            "repos": [{"source": "github_code", "repo": "o/r", "file": ".env", "url": "u1"}],
        },
        {
            "key": "deadbeefdeadbeefdeadbeefdeadbeef.AbCdEfGhIjKlMnOp",
            "valid": False,
            "repos": [],
        },
    ]
    grouped = ScannerEngine.group_keys_from_saved_results(records, valid_only=True)
    assert list(grouped) == [VALID_KEY]
    assert len(grouped[VALID_KEY]["repos"]) == 1


def test_quota_endpoint_bases_cover_cn_and_intl():
    assert "https://open.bigmodel.cn" in QUOTA_ENDPOINT_BASES
    assert "https://api.z.ai" in QUOTA_ENDPOINT_BASES


async def _verify_quota_multi_base():
    engine = ScannerEngine()
    session = object()
    quota_payload = {
        "success": True,
        "data": {
            "level": "lite",
            "limits": [{"type": "TOKENS_LIMIT", "remaining": 1000, "percentage": 10}],
        },
    }
    responses = [
        (404, None),
        (200, {"success": False}),
        (200, quota_payload),
    ]
    with patch.object(engine, "_request_verify", new=AsyncMock(side_effect=responses)) as mock_req:
        result = await engine._verify_quota(session, VALID_KEY)

    assert result["valid"] is True
    assert result["balance_kind"] == "quota"
    assert mock_req.call_count == 3
    bases = [call.kwargs["base"] for call in mock_req.call_args_list]
    assert bases == list(QUOTA_ENDPOINT_BASES)


def test_verify_quota_tries_multiple_bases():
    asyncio.run(_verify_quota_multi_base())


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
        "balance_kind": "cash",
        "provider": "zhipu",
        "reason": "",
        "repos": [{"repo": "o/r"}],
        "verified_at": "2026-06-25T00:00:00",
    }
    engine.save_results([result])

    assert (tmp_path / f"{RESULT_BASENAME}.json").exists()
    assert (tmp_path / f"{RESULT_BASENAME}.csv").exists()
    csv_text = (tmp_path / f"{RESULT_BASENAME}.csv").read_text(encoding="utf-8")
    assert VALID_KEY in csv_text
    assert (tmp_path / f"{RESULT_BASENAME}.md").exists()
    md_text = (tmp_path / f"{RESULT_BASENAME}.md").read_text(encoding="utf-8")
    assert VALID_KEY not in md_text
    assert "Cash balance keys:" in md_text
    assert "CNY 12.5000" in md_text
