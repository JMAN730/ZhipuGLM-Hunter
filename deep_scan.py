#!/usr/bin/env python3
"""Run a configurable ZhipuGLM Hunter scan."""

from __future__ import annotations

import argparse

from scanner_engine import ScannerEngine, load_queries


def _print_summary(results: list[dict], label: str):
    valid = [result for result in results if result.get("valid")]
    print(f"{label} complete. Candidates: {len(results)} | Live keys: {len(valid)}")


def main():
    parser = argparse.ArgumentParser(description="Run a configurable ZhipuGLM Hunter scan.")
    parser.add_argument("--hours", type=float, default=1.0, help="Maximum scan duration in hours.")
    parser.add_argument("--pages", type=int, default=2, help="GitHub Code Search pages per query.")
    parser.add_argument("--concurrency", type=int, default=8, help="Concurrent fetch/verify requests.")
    parser.add_argument("--query-file", default="queries_v4.txt", help="Query file to load.")
    parser.add_argument("--max-valid-keys", type=int, default=0, help="Stop after this many valid keys; 0 disables.")
    parser.add_argument(
        "--no-balance",
        action="store_true",
        help="Liveness check only (/models); skip Coding Plan quota inspection.",
    )
    parser.add_argument(
        "--sources",
        default="github_code,github_commits,github_issues",
        help="Comma-separated GitHub sources to scan (github_code,github_commits,github_issues).",
    )
    # Responsible disclosure (off by default). ScannerEngine reads these via
    # disclosure.disclose_options(); declared here so argparse accepts them and
    # they show up in --help.
    parser.add_argument(
        "--disclose",
        action="store_true",
        help="Notify owners of live keys by opening a GitHub issue on each affected repo (dry-run: only prints).",
    )
    parser.add_argument(
        "--disclose-send",
        action="store_true",
        help="Actually post disclosure issues (without this, --disclose only prints what it would post).",
    )
    parser.add_argument(
        "--disclose-max-repo-age-days",
        type=int,
        default=0,
        metavar="N",
        help="Only disclose repos pushed within the last N days (0 = no age gate).",
    )
    parser.add_argument(
        "--resume",
        action="store_true",
        help="Resume the most recent unfinished run with the same config (skip already-done queries).",
    )
    parser.add_argument(
        "--no-state",
        action="store_true",
        help="Disable the durable state DB (no checkpoint/resume, dedup, or liveness cache).",
    )
    args = parser.parse_args()

    sources = [source.strip() for source in args.sources.split(",") if source.strip()]
    engine = ScannerEngine(
        concurrency=args.concurrency,
        timeout=20,
        search_delay=6.0,
        scan_pages=args.pages,
        max_duration=int(args.hours * 3600),
        max_valid_keys=args.max_valid_keys,
        output_dir="results",
        sources=sources,
        resume=args.resume,
        use_state=not args.no_state,
        check_balance=not args.no_balance,
    )
    results = engine.run(load_queries(args.query_file))
    _print_summary(results, "Deep scan")


if __name__ == "__main__":
    main()
