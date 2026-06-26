#!/usr/bin/env python3
"""Run a small ZhipuGLM Hunter scan."""

from __future__ import annotations

from scanner_engine import BUILTIN_QUERIES, ScannerEngine


def _print_summary(results: list[dict], label: str):
    valid = [result for result in results if result.get("valid")]
    print(f"{label} complete. Candidates: {len(results)} | Live keys: {len(valid)}")


def main():
    queries = BUILTIN_QUERIES[:12]
    engine = ScannerEngine(
        concurrency=6,
        timeout=20,
        search_delay=4.0,
        scan_pages=1,
        max_duration=15 * 60,
        output_dir="results",
    )
    results = engine.run(queries)
    _print_summary(results, "Quick scan")


if __name__ == "__main__":
    main()
