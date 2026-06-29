#!/usr/bin/env python3
"""Re-check cash balance and Coding Plan quota for saved Zhipu keys."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from scanner_engine import ScannerEngine, is_bad_key


def _load_keys_from_file(path: Path) -> dict:
    grouped: dict[str, dict] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        key = line.strip()
        if not key or key.startswith("#") or is_bad_key(key):
            continue
        grouped.setdefault(key, {"repos": []})
    return grouped


def _load_keys_from_json(path: Path, valid_only: bool) -> dict:
    records = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(records, list):
        raise ValueError(f"Expected a JSON array in {path}")
    return ScannerEngine.group_keys_from_saved_results(records, valid_only=valid_only)


def _print_summary(results: list[dict]):
    valid = [result for result in results if result.get("valid")]
    cash = [result for result in valid if result.get("balance_kind") == "cash"]
    quota = [result for result in valid if result.get("balance_kind") == "quota"]
    unavailable = [result for result in valid if result.get("balance_unavailable")]
    print(
        f"Balance check complete. Candidates: {len(results)} | Live: {len(valid)} | "
        f"Cash: {len(cash)} | Quota: {len(quota)} | Unavailable: {len(unavailable)}"
    )


def main():
    parser = argparse.ArgumentParser(description="Re-check balances for saved Zhipu API keys.")
    parser.add_argument(
        "json_file",
        nargs="?",
        help="Path to zhipu_keys_result.json (or another exported JSON array).",
    )
    parser.add_argument(
        "--keys-file",
        help="Newline-delimited file of raw API keys (instead of JSON).",
    )
    parser.add_argument(
        "--valid-only",
        action="store_true",
        help="When reading JSON, only re-check keys marked valid.",
    )
    parser.add_argument("--concurrency", type=int, default=8, help="Concurrent verify requests.")
    parser.add_argument("--output-dir", default="results", help="Directory for updated exports.")
    parser.add_argument(
        "--no-balance",
        action="store_true",
        help="Liveness check only (/models); skip Coding Plan quota inspection.",
    )
    args = parser.parse_args()

    if args.keys_file:
        grouped = _load_keys_from_file(Path(args.keys_file))
    elif args.json_file:
        grouped = _load_keys_from_json(Path(args.json_file), valid_only=args.valid_only)
    else:
        parser.error("Provide json_file or --keys-file")

    if not grouped:
        print("No keys to verify.")
        return

    engine = ScannerEngine(
        concurrency=args.concurrency,
        timeout=20,
        output_dir=args.output_dir,
        check_balance=not args.no_balance,
    )
    results = engine.verify_keys(grouped)
    engine.save_results(results)
    _print_summary(results)


if __name__ == "__main__":
    main()
