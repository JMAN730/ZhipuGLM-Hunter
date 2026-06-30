"""Offline tests for verify_balances.py."""

import json
from pathlib import Path

from verify_balances import _load_keys_from_json

VALID_KEY = "a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6.AbCdEfGhIjKlMnOp"


def test_load_keys_from_json_valid_only(tmp_path: Path):
    payload = [
        {"key": VALID_KEY, "valid": True, "repos": [{"repo": "o/r"}]},
        {"key": "deadbeefdeadbeefdeadbeefdeadbeef.AbCdEfGhIjKlMnOp", "valid": False, "repos": []},
    ]
    path = tmp_path / "results.json"
    path.write_text(json.dumps(payload), encoding="utf-8")

    grouped = _load_keys_from_json(path, valid_only=True)
    assert list(grouped) == [VALID_KEY]
    assert grouped[VALID_KEY]["repos"][0]["repo"] == "o/r"
