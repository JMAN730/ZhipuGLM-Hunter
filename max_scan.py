#!/usr/bin/env python3
"""High-throughput scan — full query library, deep pagination."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from scanner_engine import ScannerEngine, load_queries


def _merge_results(existing: dict, results: list[dict]) -> dict:
    for result in results:
        key = result.get("key", "")
        if not key:
            continue
        if key not in existing:
            existing[key] = result
            continue
        old = existing[key]
        if result.get("valid") and not old.get("valid"):
            existing[key] = result
            continue
        old_repos = {(r.get("source"), r.get("repo"), r.get("file"), r.get("url")) for r in old.get("repos", [])}
        for repo in result.get("repos", []):
            entry = (repo.get("source"), repo.get("repo"), repo.get("file"), repo.get("url"))
            if entry not in old_repos:
                old.setdefault("repos", []).append(repo)
    return existing


def main():
    parser = argparse.ArgumentParser(description="Max-throughput ZhipuGLM Hunter scan.")
    parser.add_argument("--hours", type=float, default=2.0, help="Maximum scan duration in hours.")
    parser.add_argument("--pages", type=int, default=5, help="GitHub search pages per query.")
    parser.add_argument("--concurrency", type=int, default=25, help="Concurrent fetch/verify requests.")
    parser.add_argument("--query-file", default="queries_v4.txt", help="Query file to load.")
    parser.add_argument("--merge-from", default="", help="Existing JSON export to merge into.")
    parser.add_argument(
        "--sources",
        default="github_code,github_commits,github_issues",
        help="Comma-separated sources (add github_gist,github_events optionally).",
    )
    parser.add_argument("--no-balance", action="store_true", help="Liveness only; skip quota inspection.")
    args = parser.parse_args()

    existing: dict = {}
    if args.merge_from:
        merge_path = Path(args.merge_from)
        if merge_path.exists():
            for row in json.loads(merge_path.read_text(encoding="utf-8")):
                existing[row.get("key", "")] = row

    sources = [source.strip() for source in args.sources.split(",") if source.strip()]
    engine = ScannerEngine(
        concurrency=args.concurrency,
        timeout=20,
        search_delay=4.0,
        scan_pages=args.pages,
        max_duration=int(args.hours * 3600),
        output_dir="results",
        sources=sources,
        check_balance=not args.no_balance,
    )
    results = engine.run(load_queries(args.query_file))
    merged = _merge_results(existing, results)
    final = list(merged.values())
    engine.save_results(final)
    valid = [row for row in final if row.get("valid")]
    print(f"Max scan complete. Total keys: {len(final)} | Live: {len(valid)}")


if __name__ == "__main__":
    main()
