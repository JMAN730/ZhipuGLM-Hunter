#!/usr/bin/env python3
"""MVP wrapper for the planned full ZhipuGLM scan."""

from __future__ import annotations

import argparse

from scanner_engine import ScannerEngine, load_queries


def _print_summary(results: list[dict], label: str):
    valid = [result for result in results if result.get("valid")]
    print(f"{label} complete. Candidates: {len(results)} | Live keys: {len(valid)}")


def main():
    parser = argparse.ArgumentParser(description="Run the full ZhipuGLM Hunter scan.")
    parser.add_argument(
        "--no-balance",
        action="store_true",
        help="Liveness check only (/models); skip Coding Plan quota inspection.",
    )
    args = parser.parse_args()

    engine = ScannerEngine(
        concurrency=10,
        timeout=25,
        search_delay=6.0,
        scan_pages=3,
        max_duration=12 * 60 * 60,
        output_dir="results",
        check_balance=not args.no_balance,
    )
    results = engine.run(load_queries())
    _print_summary(results, "Ultimate MVP scan")


if __name__ == "__main__":
    main()
