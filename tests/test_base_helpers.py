"""Tests for scanner helper functions."""

from scanners.base import dedup_results, extract_keys, is_bad_key, redact_key

VALID_KEY = "a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6.AbCdEfGhIjKlMnOp"


def test_valid_zhipu_key_is_not_bad():
    assert is_bad_key(VALID_KEY) is False


def test_non_zhipu_sk_key_is_bad():
    assert is_bad_key("sk-aB3dE5fG7hJ9kL1mN3pQ5rS7tU9vW1xY") is True


def test_placeholder_and_low_entropy_keys_are_bad():
    assert is_bad_key("00000000000000000000000000000000.AbCdEfGhIjKl")
    assert is_bad_key("a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6.exampleSecret")


def test_extract_keys_finds_valid_and_drops_bad():
    text = f"good={VALID_KEY} bad=00000000000000000000000000000000.AbCdEfGhIjKl"
    assert extract_keys(text) == [VALID_KEY]


def test_redact_key_preserves_shape_without_exposing_full_key():
    redacted = redact_key(VALID_KEY)
    assert redacted.startswith("a1b2c3d4...")
    assert redacted.endswith("...MnOp")
    assert VALID_KEY not in redacted


def test_dedup_results_collapses_identical_source_key_url():
    results = [
        {"source": "github", "key": VALID_KEY, "url": "u1"},
        {"source": "github", "key": VALID_KEY, "url": "u1"},
        {"source": "github", "key": VALID_KEY, "url": "u2"},
    ]
    assert len(dedup_results(results)) == 2
