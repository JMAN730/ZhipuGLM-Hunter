#!/usr/bin/env python3
"""ZhipuGLM Hunter CLI — search, verify, and optional disclosure in one entry point."""

from __future__ import annotations

import argparse
import json
import webbrowser
from pathlib import Path

from scanner_engine import (
    ALL_GITHUB_SOURCES,
    DEFAULT_SOURCES,
    ScannerEngine,
    load_queries,
)


def _print_summary(results: list[dict], label: str):
    valid = [result for result in results if result.get("valid")]
    print(f"{label} complete. Candidates: {len(results)} | Live keys: {len(valid)}")


def _verify_only(path: Path, args) -> list[dict]:
    records = json.loads(path.read_text(encoding="utf-8"))
    grouped = ScannerEngine.group_keys_from_saved_results(records, valid_only=args.valid_only)
    engine = ScannerEngine(
        concurrency=args.concurrency,
        timeout=args.timeout,
        output_dir=args.output_dir,
        check_balance=not args.no_balance,
    )
    results = engine.verify_keys(grouped)
    engine.save_results(results)
    return results


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="zhipu-key-hunter",
        description="ZhipuGLM Hunter — scan public GitHub for exposed Zhipu/GLM API keys.",
    )
    parser.add_argument("-c", "--concurrency", type=int, default=8, help="Concurrent requests.")
    parser.add_argument("--timeout", type=int, default=20, help="HTTP timeout seconds.")
    parser.add_argument("--output-dir", default="results", help="Output directory.")
    parser.add_argument("--search-delay", type=float, default=4.0, help="Delay between queries.")
    parser.add_argument("--scan-pages", type=int, default=2, help="Pages per query/source.")
    parser.add_argument("--hours", type=float, default=1.0, help="Max scan duration hours.")
    parser.add_argument("--max-valid-keys", type=int, default=0, help="Stop after N live keys.")
    parser.add_argument("--queries-file", default="queries_v4.txt", help="Code search query file.")
    parser.add_argument(
        "--sources",
        default=",".join(DEFAULT_SOURCES),
        help=f"Comma-separated sources. All: {','.join(ALL_GITHUB_SOURCES)}",
    )
    parser.add_argument("--no-balance", action="store_true", help="Liveness only; skip quota.")
    parser.add_argument("--verify-only", metavar="JSON", help="Re-verify keys from a saved JSON export.")
    parser.add_argument("--valid-only", action="store_true", help="With --verify-only, only valid keys.")
    parser.add_argument(
        "--monitor",
        action="store_true",
        help="Poll GitHub Events (sets sources to github_events, uses --scan-pages as poll count).",
    )
    parser.add_argument("--disclose", action="store_true", help="Disclosure dry-run.")
    parser.add_argument("--disclose-send", action="store_true", help="Post disclosure issues.")
    parser.add_argument("--disclose-max-repo-age-days", type=int, default=0, help="Repo age gate.")
    parser.add_argument("--cmd-gen", action="store_true", help="Open cmd_generator.html in a browser.")
    return parser


def main():
    parser = build_parser()
    args = parser.parse_args()

    if args.cmd_gen:
        html = Path(__file__).resolve().parent / "cmd_generator.html"
        webbrowser.open(html.as_uri())
        print(f"Opened {html}")
        return

    if args.verify_only:
        results = _verify_only(Path(args.verify_only), args)
        _print_summary(results, "Verify-only")
        return

    sources = ["github_events"] if args.monitor else [s.strip() for s in args.sources.split(",") if s.strip()]
    engine = ScannerEngine(
        concurrency=args.concurrency,
        timeout=args.timeout,
        search_delay=args.search_delay,
        scan_pages=args.scan_pages,
        max_duration=int(args.hours * 3600),
        max_valid_keys=args.max_valid_keys,
        output_dir=args.output_dir,
        sources=sources,
        check_balance=not args.no_balance,
        auto_disclose=args.disclose or args.disclose_send,
        disclose_dry_run=not args.disclose_send,
        disclose_max_repo_age_days=args.disclose_max_repo_age_days,
    )
    results = engine.run(load_queries(args.queries_file))
    _print_summary(results, "Scan")


if __name__ == "__main__":
    main()
