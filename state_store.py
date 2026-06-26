"""Durable SQLite state for long-running scans: checkpoint/resume,
cross-run finding dedup + liveness caching, and rate-limit windows.

Synchronous on purpose: the engine is a single-threaded async pipeline, so
coroutines serialize naturally and indexed lookups are sub-millisecond.
"""

from __future__ import annotations

import os
import sqlite3
import time
import uuid

from scanners.base import finding_digest

SCHEMA_VERSION = 1

LIVE = "live"
DEAD = "dead"
ERROR = "error"
NOBALANCE = "nobalance"
_TRUSTED = {DEAD, NOBALANCE}  # statuses we never re-verify

_SCHEMA = """
CREATE TABLE IF NOT EXISTS runs(
    run_id TEXT PRIMARY KEY, config_sig TEXT,
    started_at REAL, finished_at REAL, status TEXT);
CREATE TABLE IF NOT EXISTS progress(
    run_id TEXT, source TEXT, query TEXT, done_at REAL,
    PRIMARY KEY(run_id, source, query));
CREATE TABLE IF NOT EXISTS findings(
    digest TEXT PRIMARY KEY, source TEXT, key TEXT, url TEXT,
    repo TEXT, file TEXT, status TEXT, first_seen REAL, last_seen REAL);
CREATE TABLE IF NOT EXISTS run_findings(
    run_id TEXT, digest TEXT, PRIMARY KEY(run_id, digest));
CREATE TABLE IF NOT EXISTS key_liveness(
    key TEXT PRIMARY KEY, status TEXT, checked_at REAL);
CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT);
"""


class StateStore:
    def __init__(self, path: str = "results/state.db", use_state: bool = True):
        self.path = path if use_state else ":memory:"
        if use_state:
            os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        self._conn = self._connect(self.path)
        self._init_schema()

    def _connect(self, path: str) -> sqlite3.Connection:
        try:
            conn = sqlite3.connect(path)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("SELECT count(*) FROM sqlite_master")  # smoke-test readability
            return conn
        except sqlite3.DatabaseError:
            if path != ":memory:" and os.path.exists(path):
                os.rename(path, f"{path}.corrupt-{int(time.time())}")
            conn = sqlite3.connect(path)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL")
            return conn

    def _init_schema(self) -> None:
        self._conn.executescript(_SCHEMA)
        self._conn.execute(
            "INSERT OR IGNORE INTO meta(key, value) VALUES('schema_version', ?)",
            (str(SCHEMA_VERSION),),
        )
        self._conn.commit()

    def start_or_resume_run(self, config_sig: str, resume: bool = False) -> str:
        if resume:
            row = self._conn.execute(
                "SELECT run_id FROM runs WHERE config_sig=? AND status='running' "
                "ORDER BY started_at DESC LIMIT 1",
                (config_sig,),
            ).fetchone()
            if row:
                return row["run_id"]
        run_id = f"run-{int(time.time() * 1000)}-{uuid.uuid4().hex[:8]}"
        self._conn.execute(
            "INSERT INTO runs(run_id, config_sig, started_at, status) VALUES(?,?,?,'running')",
            (run_id, config_sig, time.time()),
        )
        self._conn.commit()
        return run_id

    def finish_run(self, run_id: str) -> None:
        self._conn.execute(
            "UPDATE runs SET status='done', finished_at=? WHERE run_id=?",
            (time.time(), run_id),
        )
        self._conn.commit()

    def is_query_done(self, run_id: str, source: str, query: str) -> bool:
        row = self._conn.execute(
            "SELECT 1 FROM progress WHERE run_id=? AND source=? AND query=?",
            (run_id, source, query),
        ).fetchone()
        return row is not None

    def mark_query_done(self, run_id: str, source: str, query: str) -> None:
        self._conn.execute(
            "INSERT OR REPLACE INTO progress(run_id, source, query, done_at) VALUES(?,?,?,?)",
            (run_id, source, query, time.time()),
        )
        self._conn.commit()

    def record_finding(self, run_id: str, finding: dict) -> None:
        digest = finding_digest(finding)
        now = time.time()
        self._conn.execute(
            "INSERT INTO findings(digest, source, key, url, repo, file, status, first_seen, last_seen) "
            "VALUES(?,?,?,?,?,?,'',?,?) "
            "ON CONFLICT(digest) DO UPDATE SET last_seen=excluded.last_seen",
            (
                digest,
                finding.get("source", ""),
                finding.get("key", ""),
                finding.get("url", ""),
                finding.get("repo", ""),
                finding.get("file", ""),
                now,
                now,
            ),
        )
        self._conn.execute(
            "INSERT OR IGNORE INTO run_findings(run_id, digest) VALUES(?,?)",
            (run_id, digest),
        )
        self._conn.commit()

    def iter_run_findings(self, run_id: str) -> list[dict]:
        rows = self._conn.execute(
            "SELECT f.source, f.key, f.url, f.repo, f.file "
            "FROM findings f JOIN run_findings r ON r.digest = f.digest "
            "WHERE r.run_id = ?",
            (run_id,),
        ).fetchall()
        return [
            {"source": r["source"], "key": r["key"], "url": r["url"], "repo": r["repo"], "file": r["file"]}
            for r in rows
        ]

    def cached_liveness(self, key: str) -> str | None:
        row = self._conn.execute("SELECT status FROM key_liveness WHERE key=?", (key,)).fetchone()
        return row["status"] if row else None

    def upsert_liveness(self, key: str, status: str) -> None:
        self._conn.execute(
            "INSERT INTO key_liveness(key, status, checked_at) VALUES(?,?,?) "
            "ON CONFLICT(key) DO UPDATE SET status=excluded.status, checked_at=excluded.checked_at",
            (key, status, time.time()),
        )
        self._conn.commit()

    def should_verify(self, key: str) -> bool:
        return self.cached_liveness(key) not in _TRUSTED

    def get_block_until(self, resource: str) -> float | None:
        row = self._conn.execute(
            "SELECT value FROM meta WHERE key=?", (f"block_until:{resource}",)
        ).fetchone()
        return float(row["value"]) if row else None

    def set_block_until(self, resource: str, ts: float) -> None:
        self._conn.execute(
            "INSERT INTO meta(key, value) VALUES(?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (f"block_until:{resource}", str(ts)),
        )
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()
