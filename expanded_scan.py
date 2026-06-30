#!/usr/bin/env python3
"""Expanded scan — high-yield query subset plus optional extra GitHub sources."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from scanner_engine import RESULT_BASENAME, ScannerEngine, load_queries

HIGH_YIELD_QUERIES = [
    "zhipu api key filename:env",
    "ZHIPU_API_KEY filename:env",
    "zhipu api key filename:env.local",
    "zhipu api key filename:env.production",
    "zhipu api key filename:py",
    "open.bigmodel.cn filename:py",
    "zhipu api key filename:js",
    "zhipu api key filename:ts",
    "zhipu filename:Dockerfile",
    "zhipu filename:.github/workflows",
    "zhipu filename:docker-compose.yml",
    "zhipu api key pushed:>2026-01-01",
    "zhipu api key created:>2026-01-01",
    "langchain zhipu api_key",
    "dify zhipu api_key",
    "litellm zhipu api_key",
    "智谱 api key",
    "智谱 filename:env",
]


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
    parser = argparse.ArgumentParser(description="Expanded ZhipuGLM Hunter scan.")
    parser.add_argument("--hours", type=float, default=1.0, help="Max duration per phase in hours.")
    parser.add_argument("--pages", type=int, default=3, help="Pages per query/source.")
    parser.add_argument("--concurrency", type=int, default=15, help="Concurrent requests.")
    parser.add_argument(
        "--extra-sources",
        default="github_gist",
        help="Comma-separated optional sources for phase 2 (e.g. github_gist,github_events).",
    )
    parser.add_argument("--merge-from", default=f"results/{RESULT_BASENAME}.json", help="JSON to merge.")
    parser.add_argument("--no-balance", action="store_true", help="Liveness only; skip quota inspection.")
    args = parser.parse_args()

    existing: dict = {}
    merge_path = Path(args.merge_from)
    if merge_path.exists():
        for row in json.loads(merge_path.read_text(encoding="utf-8")):
            existing[row.get("key", "")] = row

    print("Phase 1: high-yield code queries")
    engine = ScannerEngine(
        concurrency=args.concurrency,
        timeout=20,
        search_delay=3.5,
        scan_pages=args.pages,
        max_duration=int(args.hours * 3600),
        output_dir="results",
        sources=["github_code"],
        check_balance=not args.no_balance,
    )
    phase1 = engine.run(HIGH_YIELD_QUERIES)
    _merge_results(existing, phase1)

    extra = [source.strip() for source in args.extra_sources.split(",") if source.strip()]
    if extra:
        print(f"Phase 2: optional sources {extra}")
        engine2 = ScannerEngine(
            concurrency=args.concurrency,
            timeout=20,
            search_delay=2.0,
            scan_pages=args.pages,
            max_duration=int(args.hours * 3600),
            output_dir="results",
            sources=extra,
            check_balance=not args.no_balance,
        )
        phase2 = engine2.run(load_queries())
        _merge_results(existing, phase2)

    final = list(existing.values())
    engine.save_results(final)
    valid = [row for row in final if row.get("valid")]
    print(f"Expanded scan complete. Total keys: {len(final)} | Live: {len(valid)}")


if __name__ == "__main__":
    main()
