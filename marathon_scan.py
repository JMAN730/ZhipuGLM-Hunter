#!/usr/bin/env python3
"""Continuous ("marathon") ZhipuGLM Hunter scan.

Repeats the scan on a fixed interval so freshly-leaked keys are caught — and,
with ``--disclose-send``, their owners notified — soon after they appear. The
per-repo disclosure ledger (``results/disclosed.json``) persists across cycles,
so each owner is notified at most once. Runs until Ctrl-C or ``--cycles``.

    python marathon_scan.py --interval-minutes 30 --disclose          # dry-run
    python marathon_scan.py --interval-minutes 30 --disclose-send     # notify
"""

from __future__ import annotations

import argparse
import time

from scanner_engine import ScannerEngine, load_queries


def _print_summary(results: list[dict], label: str):
    valid = [result for result in results if result.get("valid")]
    print(f"{label}: candidates {len(results)} | live keys {len(valid)}", flush=True)


def main():
    parser = argparse.ArgumentParser(description="Run ZhipuGLM Hunter continuously for ongoing disclosure.")
    parser.add_argument("--interval-minutes", type=float, default=30.0, help="Sleep between cycles.")
    parser.add_argument("--cycles", type=int, default=0, help="Number of cycles to run; 0 = run forever.")
    parser.add_argument("--hours-per-cycle", type=float, default=1.0, help="Max duration of each scan cycle.")
    parser.add_argument("--pages", type=int, default=2, help="GitHub Code Search pages per query.")
    parser.add_argument("--concurrency", type=int, default=8, help="Concurrent fetch/verify requests.")
    parser.add_argument("--query-file", default="queries_v4.txt", help="Query file to load.")
    parser.add_argument(
        "--no-balance",
        action="store_true",
        help="Liveness check only (/models); skip Coding Plan quota inspection.",
    )
    parser.add_argument(
        "--sources",
        default="github_code,github_commits,github_issues",
        help="Comma-separated GitHub sources to scan.",
    )
    # Responsible disclosure (off by default) — consumed by ScannerEngine via
    # disclosure.disclose_options(); declared here for argparse + --help.
    parser.add_argument("--disclose", action="store_true", help="Open disclosure issues in dry-run (prints only).")
    parser.add_argument("--disclose-send", action="store_true", help="Actually post disclosure issues.")
    parser.add_argument(
        "--disclose-max-repo-age-days",
        type=int,
        default=0,
        metavar="N",
        help="Only disclose repos pushed within the last N days (0 = no age gate).",
    )
    args = parser.parse_args()

    sources = [source.strip() for source in args.sources.split(",") if source.strip()]
    queries = load_queries(args.query_file)

    cycle = 0
    try:
        while args.cycles == 0 or cycle < args.cycles:
            cycle += 1
            print(f"=== marathon cycle {cycle} ===", flush=True)
            engine = ScannerEngine(
                concurrency=args.concurrency,
                timeout=20,
                search_delay=6.0,
                scan_pages=args.pages,
                max_duration=int(args.hours_per_cycle * 3600),
                output_dir="results",
                sources=sources,
                check_balance=not args.no_balance,
            )
            results = engine.run(queries)
            _print_summary(results, f"cycle {cycle}")

            if args.cycles and cycle >= args.cycles:
                break
            time.sleep(max(0.0, args.interval_minutes * 60))
    except KeyboardInterrupt:
        print("\nmarathon scan stopped.", flush=True)


if __name__ == "__main__":
    main()
